param(
  [Parameter(Mandatory = $true)][string]$BackendConfig,
  [Parameter(Mandatory = $true)][string]$VariablesFile,
  [Parameter(Mandatory = $true)][string]$ProviderSecretsFile,
  [Parameter(Mandatory = $true)][string]$ImageTag
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$terraformDirectory = Join-Path $root "infra/aws"
if ($ImageTag -notmatch '^[0-9a-f]{40}([0-9a-f]{24})?$') {
  throw "ImageTag must be a full 40- or 64-character lowercase commit SHA"
}

foreach ($command in "aws", "docker", "terraform") {
  if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
    throw "$command is required"
  }
}
foreach ($file in $BackendConfig, $VariablesFile, $ProviderSecretsFile) {
  if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Required file does not exist: $file" }
}

$variables = Get-Content -LiteralPath $VariablesFile -Raw
try {
  $providerSecrets = Get-Content -LiteralPath $ProviderSecretsFile -Raw | ConvertFrom-Json
} catch {
  throw "Provider secrets file is not valid JSON"
}

function Test-SecretValue([object]$Value) {
  if ($Value -isnot [string] -or $Value.Length -lt 12) { return $false }
  return $Value -notmatch '(?i)REPLACE|CHANGE[_-]?ME|TODO|EXAMPLE'
}

$requiredSecrets = @("CLERK_SECRET_KEY", "EASTMED_ANTHROPIC_API_KEY")
if ($variables -match '(?m)^\s*enable_postmark\s*=\s*true\s*$') {
  $requiredSecrets += "EASTMED_POSTMARK_SERVER_TOKEN"
  if ($variables -notmatch '(?m)^\s*postmark_from_email\s*=\s*"[^"@]+@[^"@]+"\s*$') {
    throw "Enabled Postmark requires postmark_from_email in the variables file"
  }
}
if ($variables -match '(?m)^\s*enable_telegram\s*=\s*true\s*$') {
  $requiredSecrets += "EASTMED_TELEGRAM_CUSTOMER_BOT_TOKEN"
}
if ($variables -match '(?m)^\s*enable_whatsapp\s*=\s*true\s*$') {
  $requiredSecrets += @(
    "EASTMED_WHATSAPP_360DIALOG_API_KEY",
    "EASTMED_WHATSAPP_WEBHOOK_PASSWORD"
  )
  if ($variables -notmatch '(?m)^\s*whatsapp_webhook_username\s*=\s*"(?!REPLACE)[^"]+"\s*$') {
    throw "Enabled WhatsApp requires a non-placeholder webhook username"
  }
}
if ($variables -match '(?m)^\s*enable_sentry\s*=\s*true\s*$') {
  $requiredSecrets += "EASTMED_SENTRY_DSN"
}
foreach ($secretName in $requiredSecrets) {
  $value = $providerSecrets.PSObject.Properties[$secretName].Value
  if (-not (Test-SecretValue $value)) { throw "Missing or placeholder provider secret: $secretName" }
}

aws sts get-caller-identity | Out-Null
if ($LASTEXITCODE -ne 0) { throw "AWS identity verification failed" }
docker info | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Docker daemon is unavailable" }

terraform -chdir=$terraformDirectory init -reconfigure -backend-config=$BackendConfig
terraform -chdir=$terraformDirectory validate
terraform -chdir=$terraformDirectory apply -var-file=$VariablesFile -var="image_tag=$ImageTag" -var="activate_services=false"

$runtimeSecret = terraform -chdir=$terraformDirectory output -raw runtime_secret_arn
aws secretsmanager put-secret-value --secret-id $runtimeSecret --secret-string "file://$ProviderSecretsFile" | Out-Null

$region = terraform -chdir=$terraformDirectory output -raw aws_region
if (-not $region) { throw "Terraform AWS region output is unavailable" }
$account = aws sts get-caller-identity --query Account --output text
aws ecr get-login-password --region $region | docker login --username AWS --password-stdin "$account.dkr.ecr.$region.amazonaws.com" | Out-Null

$apiRepository = terraform -chdir=$terraformDirectory output -raw api_repository_url
$webRepository = terraform -chdir=$terraformDirectory output -raw web_repository_url
$publishableMatch = [regex]::Match($variables, 'clerk_publishable_key\s*=\s*"([^"]+)"')
if (-not $publishableMatch.Success -or $publishableMatch.Groups[1].Value -match '(?i)REPLACE') {
  throw "A non-placeholder clerk_publishable_key is required"
}
$publishableKey = $publishableMatch.Groups[1].Value

docker build --pull --file (Join-Path $root "apps/api/Dockerfile") --tag "$apiRepository`:$ImageTag" $root
docker build --pull --file (Join-Path $root "apps/web/Dockerfile") --build-arg "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=$publishableKey" --build-arg "NEXT_PUBLIC_CLERK_SIGN_IN_URL=/sign-in" --tag "$webRepository`:$ImageTag" $root
docker push "$apiRepository`:$ImageTag"
docker push "$webRepository`:$ImageTag"

terraform -chdir=$terraformDirectory apply -var-file=$VariablesFile -var="image_tag=$ImageTag" -var="activate_services=false"

$cluster = terraform -chdir=$terraformDirectory output -raw ecs_cluster_name
$subnets = terraform -chdir=$terraformDirectory output -json private_subnet_ids | ConvertFrom-Json
$securityGroup = terraform -chdir=$terraformDirectory output -raw application_security_group_id
$network = "awsvpcConfiguration={subnets=[$($subnets -join ',')],securityGroups=[$securityGroup],assignPublicIp=DISABLED}"

function Invoke-OneOffTask([string]$TaskDefinition, [string]$Label) {
  $taskArn = aws ecs run-task --cluster $cluster --task-definition $TaskDefinition --launch-type FARGATE --network-configuration $network --query "tasks[0].taskArn" --output text
  if ($LASTEXITCODE -ne 0 -or -not $taskArn -or $taskArn -eq "None") { throw "$Label failed to start" }
  aws ecs wait tasks-stopped --cluster $cluster --tasks $taskArn
  $exitCode = aws ecs describe-tasks --cluster $cluster --tasks $taskArn --query "tasks[0].containers[0].exitCode" --output text
  if ($exitCode -ne "0") {
    $reason = aws ecs describe-tasks --cluster $cluster --tasks $taskArn --query "tasks[0].stoppedReason" --output text
    throw "$Label failed with exit $exitCode`: $reason"
  }
}

Invoke-OneOffTask (terraform -chdir=$terraformDirectory output -raw migration_task_definition) "Database migration"
Invoke-OneOffTask (terraform -chdir=$terraformDirectory output -raw role_bootstrap_task_definition) "Database role bootstrap"

terraform -chdir=$terraformDirectory apply -var-file=$VariablesFile -var="image_tag=$ImageTag" -var="activate_services=true"
$applicationUrl = terraform -chdir=$terraformDirectory output -raw application_url
$apiUrl = terraform -chdir=$terraformDirectory output -raw api_url
Invoke-WebRequest -UseBasicParsing -Uri "$apiUrl/health/ready" -TimeoutSec 15 | Out-Null
Invoke-WebRequest -UseBasicParsing -Uri $applicationUrl -TimeoutSec 15 | Out-Null
Write-Output "Deployment healthy with outbound delivery still disabled: $applicationUrl"

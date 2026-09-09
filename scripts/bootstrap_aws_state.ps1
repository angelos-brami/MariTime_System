param(
  [Parameter(Mandatory = $true)][string]$Region,
  [string]$Project = "eastmed",
  [string]$Environment = "production"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
  throw "AWS CLI is required"
}

$accountId = aws sts get-caller-identity --query Account --output text
if ($LASTEXITCODE -ne 0 -or -not $accountId) { throw "AWS identity verification failed" }
$bucket = "$Project-$Environment-terraform-$accountId-$Region".ToLowerInvariant()

aws s3api head-bucket --bucket $bucket 2>$null
if ($LASTEXITCODE -ne 0) {
  if ($Region -eq "us-east-1") {
    aws s3api create-bucket --bucket $bucket --region $Region | Out-Null
  } else {
    aws s3api create-bucket --bucket $bucket --region $Region --create-bucket-configuration "LocationConstraint=$Region" | Out-Null
  }
}

aws s3api put-public-access-block --bucket $bucket --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" | Out-Null
aws s3api put-bucket-versioning --bucket $bucket --versioning-configuration "Status=Enabled" | Out-Null
aws s3api put-bucket-encryption --bucket $bucket --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}' | Out-Null

Write-Output "bucket = `"$bucket`""
Write-Output "key = `"$Project/$Environment/terraform.tfstate`""
Write-Output "region = `"$Region`""
Write-Output "encrypt = true"
Write-Output "use_lockfile = true"

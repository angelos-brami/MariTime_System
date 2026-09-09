resource "aws_ecr_repository" "api" {
  name                 = "${local.name}/api"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.main.arn
  }
}

resource "aws_ecr_repository" "web" {
  name                 = "${local.name}/web"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.main.arn
  }
}

resource "aws_ecr_lifecycle_policy" "repositories" {
  for_each   = { api = aws_ecr_repository.api.name, web = aws_ecr_repository.web.name }
  repository = each.value
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Retain the newest 30 immutable releases"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 30 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_ecs_cluster" "main" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enhanced"
  }
}

resource "aws_iam_role" "ecs_execution" {
  name = "${local.name}-ecs-execution"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_execution_secrets" {
  role = aws_iam_role.ecs_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_secretsmanager_secret.infrastructure.arn, aws_secretsmanager_secret.runtime.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = [aws_kms_key.main.arn]
      }
    ]
  })
}

resource "aws_iam_role" "ecs_task" {
  name = "${local.name}-ecs-task"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_acm_certificate" "main" {
  domain_name               = var.app_domain
  subject_alternative_names = [var.api_domain]
  validation_method         = "DNS"
  lifecycle { create_before_destroy = true }
}

resource "aws_route53_record" "certificate" {
  for_each = {
    for option in aws_acm_certificate.main.domain_validation_options : option.domain_name => {
      name   = option.resource_record_name
      record = option.resource_record_value
      type   = option.resource_record_type
    }
  }
  zone_id = var.route53_zone_id
  name    = each.value.name
  type    = each.value.type
  ttl     = 60
  records = [each.value.record]
}

resource "aws_acm_certificate_validation" "main" {
  certificate_arn         = aws_acm_certificate.main.arn
  validation_record_fqdns = [for record in aws_route53_record.certificate : record.fqdn]
}

resource "aws_lb" "main" {
  name                       = substr(local.name, 0, 32)
  internal                   = false
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = aws_subnet.public[*].id
  enable_deletion_protection = var.deletion_protection
  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "web" {
  name        = substr("${local.name}-web", 0, 32)
  port        = 3000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"
  health_check {
    path                = "/"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 20
    matcher             = "200-399"
  }
  deregistration_delay = 30
}

resource "aws_lb_target_group" "api" {
  name        = substr("${local.name}-api", 0, 32)
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"
  health_check {
    path                = "/health/ready"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 15
    matcher             = "200"
  }
  deregistration_delay = 30
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.main.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

resource "aws_lb_listener_rule" "api_host" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 10
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
  condition {
    host_header {
      values = [var.api_domain]
    }
  }
}

resource "aws_route53_record" "app" {
  zone_id = var.route53_zone_id
  name    = var.app_domain
  type    = "A"
  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = true
  }
}

resource "aws_route53_record" "api" {
  zone_id = var.route53_zone_id
  name    = var.api_domain
  type    = "A"
  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = true
  }
}

locals {
  processes = {
    api = {
      image   = "api"
      command = ["uvicorn", "eastmed_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
      cpu     = 1024, memory = 2048, port = 8000, desired = 2, db_role = "api"
    }
    web = {
      image = "web", command = ["node", "apps/web/server.js"]
      cpu   = 512, memory = 1024, port = 3000, desired = 2, db_role = null
    }
    ingestion = {
      image = "api", command = ["eastmed-ingestion-worker"]
      cpu   = 1024, memory = 2048, port = null, desired = 1, db_role = "ingestion"
    }
    publication = {
      image = "api", command = ["eastmed-publication-worker"]
      cpu   = 512, memory = 1024, port = null, desired = 1, db_role = "publication"
    }
    delivery = {
      image = "api", command = ["eastmed-delivery-worker"]
      cpu   = 512, memory = 1024, port = null, desired = 1, db_role = "delivery"
    }
    analysis = {
      image = "api", command = ["eastmed-analysis-worker"]
      cpu   = 1024, memory = 2048, port = null, desired = 1, db_role = "analysis"
    }
    scheduler = {
      image = "api", command = ["eastmed-scheduler", "--interval", "30"]
      cpu   = 512, memory = 1024, port = null, desired = 1, db_role = "scheduler"
    }
  }

  common_python_environment = [
    { name = "EASTMED_ENVIRONMENT", value = "production" },
    { name = "EASTMED_DATABASE_HOST", value = aws_db_instance.postgres.address },
    { name = "EASTMED_DATABASE_PORT", value = tostring(aws_db_instance.postgres.port) },
    { name = "EASTMED_DATABASE_NAME", value = aws_db_instance.postgres.db_name },
    { name = "EASTMED_REDIS_HOST", value = aws_elasticache_replication_group.redis.primary_endpoint_address },
    { name = "EASTMED_REDIS_PORT", value = "6379" },
    { name = "EASTMED_OBJECT_STORE_ENDPOINT", value = "https://s3.${var.aws_region}.amazonaws.com" },
    { name = "EASTMED_OBJECT_STORE_BUCKET", value = aws_s3_bucket.evidence.id },
    { name = "EASTMED_OBJECT_STORE_ACCESS_KEY", value = "ecs-task-role" },
    { name = "EASTMED_PUBLIC_BASE_URL", value = "https://${var.app_domain}" },
    { name = "EASTMED_ALLOWED_FETCH_HOSTS", value = var.allowed_fetch_hosts },
    { name = "EASTMED_DESK_AUTH_MODE", value = "oidc" },
    { name = "EASTMED_DESK_OIDC_ISSUER", value = trimsuffix(var.clerk_issuer, "/") },
    { name = "EASTMED_DESK_OIDC_AUDIENCE", value = "https://${var.api_domain}" },
    { name = "EASTMED_DESK_OIDC_JWKS_URL", value = "${trimsuffix(var.clerk_issuer, "/")}/.well-known/jwks.json" },
    { name = "EASTMED_DESK_OIDC_AUTHORIZED_PARTIES", value = "https://${var.app_domain}" },
    { name = "EASTMED_DESK_OIDC_ALGORITHMS", value = "RS256" },
    { name = "EASTMED_DESK_OIDC_TOKEN_TYPES", value = "JWT" },
    { name = "EASTMED_DESK_OIDC_MFA_MAX_AGE_MINUTES", value = "10" },
    { name = "EASTMED_CLAIM_EXTRACTION_ENABLED", value = "true" },
    { name = "EASTMED_CLAIM_EXTRACTION_MODEL", value = "claude-sonnet-5" },
    { name = "EASTMED_CLAIM_EXTRACTION_SYSTEM_FINGERPRINT", value = var.claim_extraction_system_fingerprint },
    { name = "EASTMED_POSTMARK_FROM_EMAIL", value = var.enable_postmark ? var.postmark_from_email : "" },
    { name = "EASTMED_POSTMARK_MESSAGE_STREAM", value = var.postmark_message_stream },
    { name = "EASTMED_WHATSAPP_WEBHOOK_USERNAME", value = var.enable_whatsapp ? var.whatsapp_webhook_username : "" },
    { name = "EASTMED_SENTRY_TRACES_SAMPLE_RATE", value = "0.0" },
    { name = "EASTMED_OUTBOUND_ENABLED", value = "false" },
    { name = "EASTMED_CONSOLE_DEMO", value = "false" },
    { name = "EASTMED_PORTAL_DEMO", value = "false" },
  ]
}

resource "aws_cloudwatch_log_group" "process" {
  for_each          = local.processes
  name              = "/${local.name}/${each.key}"
  retention_in_days = 90
}

resource "aws_ecs_task_definition" "process" {
  for_each                 = local.processes
  family                   = "${local.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(each.value.cpu)
  memory                   = tostring(each.value.memory)
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  volume {
    name = "snapshots"
    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.snapshots.id
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.snapshots.id
        iam             = "DISABLED"
      }
    }
  }

  volume { name = "tmp" }

  container_definitions = jsonencode([
    merge(
      {
        name      = each.key
        image     = "${each.value.image == "web" ? aws_ecr_repository.web.repository_url : aws_ecr_repository.api.repository_url}:${var.image_tag}"
        essential = true
        command   = each.value.command
        environment = each.value.image == "web" ? [
          { name = "NODE_ENV", value = "production" },
          { name = "HOSTNAME", value = "0.0.0.0" },
          { name = "PORT", value = "3000" },
          { name = "EASTMED_ENVIRONMENT", value = "production" },
          { name = "EASTMED_API_BASE_URL", value = "https://${var.api_domain}" },
          { name = "EASTMED_PUBLIC_BASE_URL", value = "https://${var.app_domain}" },
          { name = "EASTMED_DESK_AUTH_MODE", value = "oidc" },
          { name = "EASTMED_CONSOLE_DEMO", value = "false" },
          { name = "EASTMED_PORTAL_DEMO", value = "false" },
          { name = "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", value = var.clerk_publishable_key },
          { name = "NEXT_PUBLIC_CLERK_SIGN_IN_URL", value = "/sign-in" },
          ] : concat(local.common_python_environment, [
            { name = "EASTMED_DATABASE_USER", value = "eastmed_${each.value.db_role}_login" },
        ])
        secrets = each.value.image == "web" ? [
          { name = "CLERK_SECRET_KEY", valueFrom = "${aws_secretsmanager_secret.runtime.arn}:CLERK_SECRET_KEY::" },
          { name = "EASTMED_DESK_API_TOKEN", valueFrom = "${aws_secretsmanager_secret.infrastructure.arn}:EASTMED_DESK_API_TOKEN::" },
          { name = "EASTMED_PORTAL_API_TOKEN", valueFrom = "${aws_secretsmanager_secret.infrastructure.arn}:EASTMED_PORTAL_API_TOKEN::" },
          ] : concat([
            { name = "EASTMED_DATABASE_PASSWORD", valueFrom = "${aws_secretsmanager_secret.infrastructure.arn}:DB_ROLE_${upper(each.value.db_role)}::" },
            { name = "EASTMED_REDIS_PASSWORD", valueFrom = "${aws_secretsmanager_secret.infrastructure.arn}:REDIS_PASSWORD::" },
            { name = "EASTMED_DESK_API_TOKEN", valueFrom = "${aws_secretsmanager_secret.infrastructure.arn}:EASTMED_DESK_API_TOKEN::" },
            { name = "EASTMED_PORTAL_API_TOKEN", valueFrom = "${aws_secretsmanager_secret.infrastructure.arn}:EASTMED_PORTAL_API_TOKEN::" },
            { name = "EASTMED_API_KEY_PEPPER", valueFrom = "${aws_secretsmanager_secret.infrastructure.arn}:EASTMED_API_KEY_PEPPER::" },
            { name = "EASTMED_OBJECT_STORE_SECRET_KEY", valueFrom = "${aws_secretsmanager_secret.infrastructure.arn}:EASTMED_OBJECT_STORE_SECRET_KEY::" },
            { name = "EASTMED_ANTHROPIC_API_KEY", valueFrom = "${aws_secretsmanager_secret.runtime.arn}:EASTMED_ANTHROPIC_API_KEY::" },
          ],
          var.enable_postmark ? [
            { name = "EASTMED_POSTMARK_SERVER_TOKEN", valueFrom = "${aws_secretsmanager_secret.runtime.arn}:EASTMED_POSTMARK_SERVER_TOKEN::" }
          ] : [],
          var.enable_telegram ? [
            { name = "EASTMED_TELEGRAM_CUSTOMER_BOT_TOKEN", valueFrom = "${aws_secretsmanager_secret.runtime.arn}:EASTMED_TELEGRAM_CUSTOMER_BOT_TOKEN::" }
          ] : [],
          var.enable_whatsapp ? [
            { name = "EASTMED_WHATSAPP_360DIALOG_API_KEY", valueFrom = "${aws_secretsmanager_secret.runtime.arn}:EASTMED_WHATSAPP_360DIALOG_API_KEY::" },
            { name = "EASTMED_WHATSAPP_WEBHOOK_PASSWORD", valueFrom = "${aws_secretsmanager_secret.runtime.arn}:EASTMED_WHATSAPP_WEBHOOK_PASSWORD::" }
          ] : [],
          var.enable_sentry ? [
            { name = "EASTMED_SENTRY_DSN", valueFrom = "${aws_secretsmanager_secret.runtime.arn}:EASTMED_SENTRY_DSN::" }
          ] : []
        )
        mountPoints = each.value.image == "web" ? [
          { sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }
          ] : each.key == "ingestion" ? [
          { sourceVolume = "snapshots", containerPath = "/app/data/snapshots", readOnly = false },
          { sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }
          ] : [
          { sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }
        ]
        readonlyRootFilesystem = true
        linuxParameters        = { initProcessEnabled = true }
        logConfiguration = {
          logDriver = "awslogs"
          options = {
            awslogs-group         = aws_cloudwatch_log_group.process[each.key].name
            awslogs-region        = var.aws_region
            awslogs-stream-prefix = each.key
          }
        }
      },
      each.value.image == "web" ? {} : { entryPoint = ["eastmed-container-entrypoint", "--"] },
      each.value.port == null ? {} : {
        portMappings = [{ containerPort = each.value.port, hostPort = each.value.port, protocol = "tcp" }]
      },
      each.key == "api" ? {
        healthCheck = {
          command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)\""]
          interval    = 15
          timeout     = 5
          retries     = 3
          startPeriod = 30
        }
      } : {},
      each.key == "web" ? {
        healthCheck = {
          command     = ["CMD-SHELL", "node -e \"fetch('http://127.0.0.1:3000').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))\""]
          interval    = 20
          timeout     = 5
          retries     = 3
          startPeriod = 30
        }
      } : {}
    )
  ])
}

resource "aws_ecs_service" "process" {
  for_each               = local.processes
  name                   = each.key
  cluster                = aws_ecs_cluster.main.id
  task_definition        = aws_ecs_task_definition.process[each.key].arn
  desired_count          = var.activate_services ? each.value.desired : 0
  launch_type            = "FARGATE"
  enable_execute_command = false
  platform_version       = "LATEST"
  propagate_tags         = "SERVICE"

  network_configuration {
    subnets = each.key == "ingestion" ? aws_subnet.ingestion[*].id : aws_subnet.private[*].id
    security_groups = [
      each.key == "ingestion" ? aws_security_group.ingestion.id : aws_security_group.application.id
    ]
    assign_public_ip = false
  }

  dynamic "load_balancer" {
    for_each = each.key == "web" ? [aws_lb_target_group.web.arn] : each.key == "api" ? [aws_lb_target_group.api.arn] : []
    content {
      target_group_arn = load_balancer.value
      container_name   = each.key
      container_port   = each.value.port
    }
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_lb_listener.https, aws_efs_mount_target.snapshots]
}

resource "aws_cloudwatch_log_group" "bootstrap" {
  name              = "/${local.name}/bootstrap"
  retention_in_days = 365
}

resource "aws_ecs_task_definition" "migration" {
  family                   = "${local.name}-migration"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn
  container_definitions = jsonencode([{
    name       = "migration"
    image      = "${aws_ecr_repository.api.repository_url}:${var.image_tag}"
    essential  = true
    entryPoint = ["eastmed-container-entrypoint", "--"]
    command    = ["alembic", "-c", "apps/api/alembic.ini", "upgrade", "head"]
    environment = [
      { name = "EASTMED_ENVIRONMENT", value = "migration" },
      { name = "EASTMED_DATABASE_HOST", value = aws_db_instance.postgres.address },
      { name = "EASTMED_DATABASE_PORT", value = "5432" },
      { name = "EASTMED_DATABASE_NAME", value = aws_db_instance.postgres.db_name },
      { name = "EASTMED_DATABASE_USER", value = aws_db_instance.postgres.username },
    ]
    secrets                = [{ name = "EASTMED_DATABASE_PASSWORD", valueFrom = "${aws_secretsmanager_secret.infrastructure.arn}:DB_MASTER_PASSWORD::" }]
    readonlyRootFilesystem = true
    logConfiguration = { logDriver = "awslogs", options = {
      awslogs-group = aws_cloudwatch_log_group.bootstrap.name, awslogs-region = var.aws_region, awslogs-stream-prefix = "migration"
    } }
  }])
}

resource "aws_ecs_task_definition" "role_bootstrap" {
  family                   = "${local.name}-role-bootstrap"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn
  container_definitions = jsonencode([{
    name       = "role-bootstrap"
    image      = "${aws_ecr_repository.api.repository_url}:${var.image_tag}"
    essential  = true
    entryPoint = ["eastmed-container-entrypoint", "--"]
    command    = ["eastmed-provision-database-roles"]
    environment = [
      { name = "EASTMED_ENVIRONMENT", value = "migration" },
      { name = "EASTMED_DATABASE_HOST", value = aws_db_instance.postgres.address },
      { name = "EASTMED_DATABASE_PORT", value = "5432" },
      { name = "EASTMED_DATABASE_NAME", value = aws_db_instance.postgres.db_name },
      { name = "EASTMED_DATABASE_USER", value = aws_db_instance.postgres.username },
    ]
    secrets = concat(
      [{ name = "EASTMED_DATABASE_PASSWORD", valueFrom = "${aws_secretsmanager_secret.infrastructure.arn}:DB_MASTER_PASSWORD::" }],
      [for service in keys(random_password.database_roles) : {
        name = "EASTMED_DB_ROLE_PASSWORD_${upper(service)}", valueFrom = "${aws_secretsmanager_secret.infrastructure.arn}:DB_ROLE_${upper(service)}::"
      }]
    )
    readonlyRootFilesystem = true
    logConfiguration = { logDriver = "awslogs", options = {
      awslogs-group = aws_cloudwatch_log_group.bootstrap.name, awslogs-region = var.aws_region, awslogs-stream-prefix = "role-bootstrap"
    } }
  }])
}

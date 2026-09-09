resource "aws_kms_key" "main" {
  description             = "${local.name} data, log, secret and backup encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true
}

resource "aws_kms_alias" "main" {
  name          = "alias/${local.name}"
  target_key_id = aws_kms_key.main.key_id
}

resource "random_password" "database_master" {
  length  = 48
  special = false
}

resource "random_password" "redis" {
  length  = 48
  special = false
}

resource "random_password" "desk_api" {
  length  = 64
  special = false
}

resource "random_password" "portal_api" {
  length  = 64
  special = false
}

resource "random_password" "api_pepper" {
  length  = 64
  special = false
}

resource "random_password" "object_store" {
  length  = 64
  special = false
}

resource "random_password" "database_roles" {
  for_each = toset(["api", "scheduler", "ingestion", "analysis", "publication", "delivery", "readonly"])
  length   = 48
  special  = false
}

resource "aws_secretsmanager_secret" "infrastructure" {
  name                    = "${local.name}/infrastructure"
  kms_key_id              = aws_kms_key.main.arn
  recovery_window_in_days = 30
}

resource "aws_secretsmanager_secret_version" "infrastructure" {
  secret_id = aws_secretsmanager_secret.infrastructure.id
  secret_string = jsonencode(merge(
    {
      DB_MASTER_PASSWORD              = random_password.database_master.result
      REDIS_PASSWORD                  = random_password.redis.result
      EASTMED_DESK_API_TOKEN          = random_password.desk_api.result
      EASTMED_PORTAL_API_TOKEN        = random_password.portal_api.result
      EASTMED_API_KEY_PEPPER          = random_password.api_pepper.result
      EASTMED_OBJECT_STORE_SECRET_KEY = random_password.object_store.result
    },
    { for service, password in random_password.database_roles : "DB_ROLE_${upper(service)}" => password.result }
  ))
}

resource "aws_secretsmanager_secret" "runtime" {
  name                    = "${local.name}/runtime-providers"
  description             = "Populate CLERK_SECRET_KEY and EASTMED_ANTHROPIC_API_KEY before service activation"
  kms_key_id              = aws_kms_key.main.arn
  recovery_window_in_days = 30
}

resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_parameter_group" "postgres" {
  name   = "${local.name}-postgres16"
  family = "postgres16"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
  parameter {
    name  = "log_connections"
    value = "1"
  }
  parameter {
    name  = "log_disconnections"
    value = "1"
  }
}

resource "aws_db_instance" "postgres" {
  identifier                      = "${local.name}-postgres"
  engine                          = "postgres"
  engine_version                  = "16"
  instance_class                  = var.database_instance_class
  allocated_storage               = 100
  max_allocated_storage           = 500
  storage_type                    = "gp3"
  storage_encrypted               = true
  kms_key_id                      = aws_kms_key.main.arn
  db_name                         = "eastmed"
  username                        = "eastmed_owner"
  password                        = random_password.database_master.result
  port                            = 5432
  multi_az                        = true
  publicly_accessible             = false
  db_subnet_group_name            = aws_db_subnet_group.main.name
  vpc_security_group_ids          = [aws_security_group.database.id]
  parameter_group_name            = aws_db_parameter_group.postgres.name
  backup_retention_period         = 35
  copy_tags_to_snapshot           = true
  deletion_protection             = var.deletion_protection
  skip_final_snapshot             = false
  final_snapshot_identifier       = "${local.name}-final"
  performance_insights_enabled    = true
  performance_insights_kms_key_id = aws_kms_key.main.arn
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
  auto_minor_version_upgrade      = true
  apply_immediately               = false

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_elasticache_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id       = "${local.name}-redis"
  description                = "EastMed queues and job state"
  engine                     = "redis"
  engine_version             = "7.1"
  node_type                  = var.redis_node_type
  port                       = 6379
  num_cache_clusters         = 2
  automatic_failover_enabled = true
  multi_az_enabled           = true
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                 = random_password.redis.result
  subnet_group_name          = aws_elasticache_subnet_group.main.name
  security_group_ids         = [aws_security_group.redis.id]
  snapshot_retention_limit   = 7
  snapshot_window            = "02:00-03:00"
  maintenance_window         = "sun:03:30-sun:04:30"
  auto_minor_version_upgrade = true
  apply_immediately          = false
}

resource "aws_efs_file_system" "snapshots" {
  encrypted        = true
  kms_key_id       = aws_kms_key.main.arn
  performance_mode = "generalPurpose"
  throughput_mode  = "elastic"
  tags             = { Name = "${local.name}-snapshots" }
}

resource "aws_efs_mount_target" "snapshots" {
  count           = 2
  file_system_id  = aws_efs_file_system.snapshots.id
  subnet_id       = aws_subnet.private[count.index].id
  security_groups = [aws_security_group.efs.id]
}

resource "aws_efs_access_point" "snapshots" {
  file_system_id = aws_efs_file_system.snapshots.id
  posix_user {
    gid = 1000
    uid = 1000
  }
  root_directory {
    path = "/eastmed"
    creation_info {
      owner_gid   = 1000
      owner_uid   = 1000
      permissions = "0750"
    }
  }
}

resource "aws_efs_backup_policy" "snapshots" {
  file_system_id = aws_efs_file_system.snapshots.id
  backup_policy { status = "ENABLED" }
}

resource "aws_backup_vault" "evidence" {
  name        = "${local.name}-evidence"
  kms_key_arn = aws_kms_key.main.arn
}

resource "aws_backup_plan" "evidence" {
  name = "${local.name}-evidence"

  rule {
    rule_name         = "daily-35-days"
    target_vault_name = aws_backup_vault.evidence.name
    schedule          = "cron(0 1 ? * * *)"
    lifecycle { delete_after = 35 }
    recovery_point_tags = {
      Workload = local.name
      Schedule = "daily"
    }
  }

  rule {
    rule_name         = "weekly-365-days"
    target_vault_name = aws_backup_vault.evidence.name
    schedule          = "cron(0 2 ? * SUN *)"
    lifecycle { delete_after = 365 }
    recovery_point_tags = {
      Workload = local.name
      Schedule = "weekly"
    }
  }
}

resource "aws_iam_role" "backup" {
  name = "${local.name}-backup"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "backup.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "backup" {
  role       = aws_iam_role.backup.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup"
}

resource "aws_backup_selection" "evidence" {
  iam_role_arn = aws_iam_role.backup.arn
  name         = "${local.name}-evidence"
  plan_id      = aws_backup_plan.evidence.id
  resources    = [aws_efs_file_system.snapshots.arn]
}

resource "aws_s3_bucket" "evidence" {
  bucket_prefix = "${local.name}-evidence-"
}

resource "aws_s3_bucket_public_access_block" "evidence" {
  bucket                  = aws_s3_bucket.evidence.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "evidence_bucket" {
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.evidence.arn, "${aws_s3_bucket.evidence.arn}/*"]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid    = "DenyUnencryptedObjectWrites"
    effect = "Deny"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.evidence.arn}/*"]
    condition {
      test     = "StringNotEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["aws:kms"]
    }
  }
}

resource "aws_s3_bucket_policy" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  policy = data.aws_iam_policy_document.evidence_bucket.json
}

resource "aws_s3_bucket_versioning" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.main.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  rule {
    id     = "retain-and-archive"
    status = "Enabled"
    filter {}
    noncurrent_version_transition {
      noncurrent_days = 30
      storage_class   = "GLACIER_IR"
    }
    noncurrent_version_expiration { noncurrent_days = 365 }
  }
}

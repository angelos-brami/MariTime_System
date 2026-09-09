output "application_url" { value = "https://${var.app_domain}" }
output "api_url" { value = "https://${var.api_domain}" }
output "aws_region" { value = var.aws_region }
output "ecs_cluster_name" { value = aws_ecs_cluster.main.name }
output "private_subnet_ids" { value = aws_subnet.private[*].id }
output "application_security_group_id" { value = aws_security_group.application.id }
output "api_repository_url" { value = aws_ecr_repository.api.repository_url }
output "web_repository_url" { value = aws_ecr_repository.web.repository_url }
output "runtime_secret_arn" { value = aws_secretsmanager_secret.runtime.arn }
output "infrastructure_secret_arn" {
  value     = aws_secretsmanager_secret.infrastructure.arn
  sensitive = true
}
output "migration_task_definition" { value = aws_ecs_task_definition.migration.arn }
output "role_bootstrap_task_definition" { value = aws_ecs_task_definition.role_bootstrap.arn }
output "alarm_topic_arn" { value = aws_sns_topic.alarms.arn }

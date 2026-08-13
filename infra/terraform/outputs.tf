output "vpc_id" {
  value = aws_vpc.main.id
}

output "private_subnet_ids" {
  value = [for subnet in aws_subnet.private : subnet.id]
}

output "workload_security_group_id" {
  description = "Background-task security group used for one-off migration tasks."
  value       = aws_security_group.background.id
}

output "service_security_group_ids" {
  value = {
    web        = aws_security_group.web.id
    api        = aws_security_group.api.id
    background = aws_security_group.background.id
    clamav     = aws_security_group.clamav.id
  }
}

output "database_endpoint" {
  value = aws_db_instance.postgres.endpoint
}

output "database_master_secret_arn" {
  value     = aws_db_instance.postgres.master_user_secret[0].secret_arn
  sensitive = true
}

output "opensearch_endpoint" {
  value = aws_opensearch_domain.main.endpoint
}

output "document_bucket" {
  value = aws_s3_bucket.documents.id
}

output "ecr_repositories" {
  value = { for name, repository in aws_ecr_repository.images : name => repository.repository_url }
}

output "ecs_cluster_arn" {
  value = aws_ecs_cluster.main.arn
}

output "application_secret_arns" {
  value = { for name, secret in aws_secretsmanager_secret.application : name => secret.arn }
}

output "application_url" {
  value = "https://${var.hostname}"
}

output "load_balancer_dns_name" {
  value = aws_lb.web.dns_name
}

output "ecs_services" {
  value = {
    api    = aws_ecs_service.api.name
    web    = aws_ecs_service.web.name
    worker = aws_ecs_service.worker.name
    clamav = aws_ecs_service.clamav.name
  }
}

output "migration_task_definition_arn" {
  value = aws_ecs_task_definition.migration.arn
}

output "scheduler_task_definition_arn" {
  value = aws_ecs_task_definition.scheduler.arn
}

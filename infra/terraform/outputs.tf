output "vpc_id" {
  value = aws_vpc.main.id
}

output "private_subnet_ids" {
  value = [for subnet in aws_subnet.private : subnet.id]
}

output "workload_security_group_id" {
  value = aws_security_group.workloads.id
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

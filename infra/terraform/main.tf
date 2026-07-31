data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = { Name = "${var.project}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.project}-igw" }
}

resource "aws_subnet" "public" {
  for_each                = toset(local.azs)
  vpc_id                  = aws_vpc.main.id
  availability_zone       = each.value
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, index(local.azs, each.value))
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.project}-public-${each.value}" }
}

resource "aws_subnet" "private" {
  for_each          = toset(local.azs)
  vpc_id            = aws_vpc.main.id
  availability_zone = each.value
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, index(local.azs, each.value) + 8)
  tags              = { Name = "${var.project}-private-${each.value}" }
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "${var.project}-nat" }
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = values(aws_subnet.public)[0].id
  depends_on    = [aws_internet_gateway.main]
  tags          = { Name = "${var.project}-nat" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
}

resource "aws_route_table_association" "public" {
  for_each       = aws_subnet.public
  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  for_each       = aws_subnet.private
  subnet_id      = each.value.id
  route_table_id = aws_route_table.private.id
}

resource "aws_security_group" "workloads" {
  name        = "${var.project}-workloads"
  description = "ECS application and scheduler workloads"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "database" {
  name        = "${var.project}-database"
  description = "PostgreSQL from application workloads"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.workloads.id]
  }
}

resource "aws_security_group" "opensearch" {
  name        = "${var.project}-opensearch"
  description = "OpenSearch HTTPS from application workloads"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.workloads.id]
  }
}

resource "aws_db_subnet_group" "main" {
  name       = var.project
  subnet_ids = [for subnet in aws_subnet.private : subnet.id]
}

resource "aws_db_instance" "postgres" {
  identifier                  = "${var.project}-postgres"
  engine                      = "postgres"
  engine_version              = "16"
  instance_class              = var.database_instance_class
  allocated_storage           = 100
  max_allocated_storage       = 1000
  storage_type                = "gp3"
  storage_encrypted           = true
  db_name                     = var.database_name
  username                    = "procintel_owner"
  manage_master_user_password = true
  db_subnet_group_name        = aws_db_subnet_group.main.name
  vpc_security_group_ids      = [aws_security_group.database.id]
  publicly_accessible         = false
  multi_az                    = true
  backup_retention_period     = 35
  backup_window               = "01:00-02:00"
  maintenance_window          = "sun:03:00-sun:04:00"
  deletion_protection         = true
  skip_final_snapshot         = false
  final_snapshot_identifier   = "${var.project}-postgres-final"
  performance_insights_enabled = true
  auto_minor_version_upgrade  = true
}

resource "aws_s3_bucket" "documents" {
  bucket_prefix = "${var.project}-documents-"
}

resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket                  = aws_s3_bucket.documents.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id
  rule {
    id     = "archive-old-versions"
    status = "Enabled"
    noncurrent_version_transition {
      noncurrent_days = 30
      storage_class   = "GLACIER_IR"
    }
    noncurrent_version_expiration { noncurrent_days = 365 }
  }
}

resource "aws_opensearch_domain" "main" {
  domain_name    = var.project
  engine_version = "OpenSearch_2.17"

  cluster_config {
    instance_type          = var.opensearch_instance_type
    instance_count         = 2
    zone_awareness_enabled = true
    zone_awareness_config { availability_zone_count = 2 }
  }

  ebs_options {
    ebs_enabled = true
    volume_type = "gp3"
    volume_size = 50
  }

  encrypt_at_rest { enabled = true }
  node_to_node_encryption { enabled = true }
  domain_endpoint_options {
    enforce_https       = true
    tls_security_policy = "Policy-Min-TLS-1-2-2019-07"
  }

  vpc_options {
    subnet_ids         = [for subnet in aws_subnet.private : subnet.id]
    security_group_ids = [aws_security_group.opensearch.id]
  }

  access_policies = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
      Action    = "es:ESHttp*"
      Resource  = "arn:aws:es:${var.aws_region}:${data.aws_caller_identity.current.account_id}:domain/${var.project}/*"
    }]
  })
}

resource "aws_ecr_repository" "images" {
  for_each             = toset(["api", "web", "scheduler"])
  name                 = "${var.project}/${each.value}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
}

resource "aws_ecs_cluster" "main" {
  name = var.project
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "services" {
  for_each          = toset(["api", "web", "scheduler"])
  name              = "/${var.project}/${each.value}"
  retention_in_days = 90
}

resource "aws_secretsmanager_secret" "application" {
  for_each = toset([
    "app-database",
    "oidc",
    "llm",
    "gemi",
    "smtp",
    "webhooks",
  ])
  name                    = "${var.project}/${each.value}"
  recovery_window_in_days = 30
}

resource "aws_sns_topic" "operations" {
  name = "${var.project}-operations"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.operations.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "database_cpu" {
  alarm_name          = "${var.project}-database-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  alarm_actions       = [aws_sns_topic.operations.arn]
  dimensions          = { DBInstanceIdentifier = aws_db_instance.postgres.id }
}

resource "aws_cloudwatch_metric_alarm" "database_storage" {
  alarm_name          = "${var.project}-database-storage"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "FreeStorageSpace"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Minimum"
  threshold           = 10737418240
  alarm_actions       = [aws_sns_topic.operations.arn]
  dimensions          = { DBInstanceIdentifier = aws_db_instance.postgres.id }
}

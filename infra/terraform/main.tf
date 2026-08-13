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

resource "aws_security_group" "web" {
  name        = "${var.project}-web"
  description = "Public web application behind the load balancer"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

}

resource "aws_security_group" "api" {
  name        = "${var.project}-api"
  description = "Private API reachable only from the web application"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description     = "API from web BFF"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.web.id]
  }
}

resource "aws_security_group" "background" {
  name        = "${var.project}-background"
  description = "Ingestion, durable workers, metrics, and migrations"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "clamav" {
  name        = "${var.project}-clamav"
  description = "ClamAV reachable only from API and background tasks"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description     = "ClamAV scanning from application tasks"
    from_port       = 3310
    to_port         = 3310
    protocol        = "tcp"
    security_groups = [aws_security_group.api.id, aws_security_group.background.id]
  }
}

resource "aws_security_group" "load_balancer" {
  name        = "${var.project}-load-balancer"
  description = "Public HTTPS entry point"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port       = 3000
    to_port         = 3000
    protocol        = "tcp"
    security_groups = [aws_security_group.web.id]
  }
}

resource "aws_security_group_rule" "web_from_load_balancer" {
  type                     = "ingress"
  from_port                = 3000
  to_port                  = 3000
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.load_balancer.id
  security_group_id        = aws_security_group.web.id
}

resource "aws_security_group" "database" {
  name        = "${var.project}-database"
  description = "PostgreSQL from application workloads"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.api.id, aws_security_group.background.id]
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
    security_groups = [aws_security_group.api.id, aws_security_group.background.id]
  }
}

resource "aws_db_subnet_group" "main" {
  name       = var.project
  subnet_ids = [for subnet in aws_subnet.private : subnet.id]
}

resource "aws_db_instance" "postgres" {
  identifier                   = "${var.project}-postgres"
  engine                       = "postgres"
  engine_version               = "16"
  instance_class               = var.database_instance_class
  allocated_storage            = 100
  max_allocated_storage        = 1000
  storage_type                 = "gp3"
  storage_encrypted            = true
  db_name                      = var.database_name
  username                     = "procintel_owner"
  manage_master_user_password  = true
  db_subnet_group_name         = aws_db_subnet_group.main.name
  vpc_security_group_ids       = [aws_security_group.database.id]
  publicly_accessible          = false
  multi_az                     = true
  backup_retention_period      = 35
  backup_window                = "01:00-02:00"
  maintenance_window           = "sun:03:00-sun:04:00"
  deletion_protection          = true
  skip_final_snapshot          = false
  final_snapshot_identifier    = "${var.project}-postgres-final"
  performance_insights_enabled = true
  auto_minor_version_upgrade   = true
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
    filter {}
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
      Effect = "Allow"
      # The domain is private and its security group admits only ECS tasks.
      # This permits unsigned clients inside that network boundary.
      Principal = { AWS = "*" }
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
  for_each          = toset(["api", "web", "scheduler", "worker", "clamav", "migration"])
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
    "commercial",
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

# Runtime IAM

resource "aws_iam_role" "ecs_execution" {
  name = "${var.project}-ecs-execution"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_execution_secrets" {
  name = "secrets"
  role = aws_iam_role.ecs_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["secretsmanager:GetSecretValue", "kms:Decrypt"]
      Resource = concat(
        [for secret in values(aws_secretsmanager_secret.application) : secret.arn],
        [aws_db_instance.postgres.master_user_secret[0].secret_arn]
      )
    }]
  })
}

resource "aws_iam_role" "ecs_task" {
  name = "${var.project}-ecs-task"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role" "web_task" {
  name = "${var.project}-web-task"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "web_task_exec" {
  name = "ecs-exec"
  role = aws_iam_role.web_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"
      ]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "ecs_task_data" {
  name = "data-plane"
  role = aws_iam_role.ecs_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject", "s3:PutObject", "s3:DeleteObject",
          "s3:AbortMultipartUpload", "s3:ListBucket"
        ]
        Resource = [aws_s3_bucket.documents.arn, "${aws_s3_bucket.documents.arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["es:ESHttpGet", "es:ESHttpHead", "es:ESHttpPost", "es:ESHttpPut", "es:ESHttpDelete"]
        Resource = "${aws_opensearch_domain.main.arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel",
          "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = "cloudwatch:PutMetricData"
        Resource = "*"
        Condition = {
          StringEquals = { "cloudwatch:namespace" = "Procintel" }
        }
      }
    ]
  })
}

# Public entry point and private service discovery

resource "aws_lb" "web" {
  name                       = substr("${var.project}-web", 0, 32)
  internal                   = false
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.load_balancer.id]
  subnets                    = [for subnet in aws_subnet.public : subnet.id]
  drop_invalid_header_fields = true
  enable_xff_client_port     = false
  xff_header_processing_mode = "append"
  enable_deletion_protection = true
}

resource "aws_lb_target_group" "web" {
  name        = substr("${var.project}-web", 0, 32)
  port        = 3000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.main.id

  health_check {
    enabled             = true
    path                = "/health"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.web.arn
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
  load_balancer_arn = aws_lb.web.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

resource "aws_route53_record" "web" {
  count   = var.hosted_zone_id == "" ? 0 : 1
  zone_id = var.hosted_zone_id
  name    = var.hostname
  type    = "A"
  alias {
    name                   = aws_lb.web.dns_name
    zone_id                = aws_lb.web.zone_id
    evaluate_target_health = true
  }
}

resource "aws_service_discovery_private_dns_namespace" "main" {
  name = "${var.project}.internal"
  vpc  = aws_vpc.main.id
}

resource "aws_service_discovery_service" "api" {
  name = "api"
  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.main.id
    dns_records {
      ttl  = 10
      type = "A"
    }
    routing_policy = "MULTIVALUE"
  }
  health_check_custom_config { failure_threshold = 1 }
}

resource "aws_service_discovery_service" "clamav" {
  name = "clamav"
  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.main.id
    dns_records {
      ttl  = 10
      type = "A"
    }
    routing_policy = "MULTIVALUE"
  }
  health_check_custom_config { failure_threshold = 1 }
}

locals {
  private_subnets = [for subnet in aws_subnet.private : subnet.id]
  common_environment = [
    { name = "PROCINTEL_ENV", value = var.environment },
    { name = "OBJECT_STORAGE_BACKEND", value = "s3" },
    { name = "OBJECT_STORAGE_BUCKET", value = aws_s3_bucket.documents.id },
    { name = "RAW_STORE_ROOT", value = "/tmp/raw" },
    { name = "DOCUMENT_STORE_ROOT", value = "/tmp/documents" },
    { name = "EXPORT_ROOT", value = "/tmp/exports" },
    { name = "OPENSEARCH_URL", value = "https://${aws_opensearch_domain.main.endpoint}" },
    { name = "AWS_REGION", value = var.aws_region },
  ]
  python_secrets = [
    { name = "DATABASE_URL", valueFrom = "${aws_secretsmanager_secret.application["app-database"].arn}:DATABASE_URL::" },
  ]
  provider_secrets = [
    { name = "GEMI_API_KEY", valueFrom = "${aws_secretsmanager_secret.application["gemi"].arn}:API_KEY::" },
    { name = "PROCINTEL_LLM_API_KEY", valueFrom = "${aws_secretsmanager_secret.application["llm"].arn}:API_KEY::" },
    { name = "PROCINTEL_LLM_ENDPOINT", valueFrom = "${aws_secretsmanager_secret.application["llm"].arn}:ENDPOINT::" },
    { name = "PROCINTEL_LLM_MODEL", valueFrom = "${aws_secretsmanager_secret.application["llm"].arn}:MODEL::" },
  ]
  delivery_secrets = [
    { name = "SMTP_HOST", valueFrom = "${aws_secretsmanager_secret.application["smtp"].arn}:HOST::" },
    { name = "SMTP_PORT", valueFrom = "${aws_secretsmanager_secret.application["smtp"].arn}:PORT::" },
    { name = "SMTP_USERNAME", valueFrom = "${aws_secretsmanager_secret.application["smtp"].arn}:USERNAME::" },
    { name = "SMTP_PASSWORD", valueFrom = "${aws_secretsmanager_secret.application["smtp"].arn}:PASSWORD::" },
    { name = "SMTP_FROM_ADDRESS", valueFrom = "${aws_secretsmanager_secret.application["smtp"].arn}:FROM_ADDRESS::" },
    { name = "SMTP_USE_TLS", valueFrom = "${aws_secretsmanager_secret.application["smtp"].arn}:USE_TLS::" },
    { name = "BID_REMINDER_EMAIL_WEBHOOK_URL", valueFrom = "${aws_secretsmanager_secret.application["webhooks"].arn}:BID_REMINDER_EMAIL_URL::" },
    { name = "BID_REMINDER_WEBHOOK_URL", valueFrom = "${aws_secretsmanager_secret.application["webhooks"].arn}:BID_REMINDER_URL::" },
  ]
  commercial_secrets = [
    { name = "STRIPE_SECRET_KEY", valueFrom = "${aws_secretsmanager_secret.application["commercial"].arn}:STRIPE_SECRET_KEY::" },
    { name = "STRIPE_WEBHOOK_SECRET", valueFrom = "${aws_secretsmanager_secret.application["commercial"].arn}:STRIPE_WEBHOOK_SECRET::" },
    { name = "STRIPE_PRICE_STARTER_MONTHLY", valueFrom = "${aws_secretsmanager_secret.application["commercial"].arn}:STRIPE_PRICE_STARTER_MONTHLY::" },
    { name = "STRIPE_PRICE_STARTER_ANNUAL", valueFrom = "${aws_secretsmanager_secret.application["commercial"].arn}:STRIPE_PRICE_STARTER_ANNUAL::" },
    { name = "STRIPE_PRICE_PROFESSIONAL_MONTHLY", valueFrom = "${aws_secretsmanager_secret.application["commercial"].arn}:STRIPE_PRICE_PROFESSIONAL_MONTHLY::" },
    { name = "STRIPE_PRICE_PROFESSIONAL_ANNUAL", valueFrom = "${aws_secretsmanager_secret.application["commercial"].arn}:STRIPE_PRICE_PROFESSIONAL_ANNUAL::" },
    { name = "CRM_HUBSPOT_WEBHOOK_URL", valueFrom = "${aws_secretsmanager_secret.application["webhooks"].arn}:CRM_HUBSPOT_URL::" },
    { name = "CRM_SALESFORCE_WEBHOOK_URL", valueFrom = "${aws_secretsmanager_secret.application["webhooks"].arn}:CRM_SALESFORCE_URL::" },
  ]
  log_options = {
    "awslogs-region"        = var.aws_region
    "awslogs-stream-prefix" = var.environment
  }
}

# ECS task definitions

resource "aws_ecs_task_definition" "api" {
  family                   = "${var.project}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  volume { name = "tmp" }
  container_definitions = jsonencode([{
    name         = "api"
    image        = "${aws_ecr_repository.images["api"].repository_url}:${var.image_tag}"
    essential    = true
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    environment = concat(local.common_environment, [
      { name = "PROCINTEL_DEV_AUTH", value = "false" },
      { name = "PROCINTEL_RATE_LIMIT_PER_MINUTE", value = tostring(var.api_rate_limit_per_minute) },
      { name = "TRUSTED_HOSTS", value = "api.${var.project}.internal,${var.hostname},localhost,127.0.0.1" },
      { name = "CORS_ORIGINS", value = "https://${var.hostname}" },
      { name = "PROCINTEL_WEB_ORIGIN", value = "https://${var.hostname}" },
      { name = "CLAMD_HOST", value = "clamav.${var.project}.internal" },
    ])
    secrets = concat(local.python_secrets, local.provider_secrets, local.commercial_secrets, [
      { name = "OIDC_ISSUER_URL", valueFrom = "${aws_secretsmanager_secret.application["oidc"].arn}:ISSUER_URL::" },
      { name = "OIDC_AUDIENCE", valueFrom = "${aws_secretsmanager_secret.application["oidc"].arn}:AUDIENCE::" },
    ])
    healthCheck = {
      command     = ["CMD-SHELL", "curl -fsS http://localhost:8000/health/ready || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }
    readonlyRootFilesystem = true
    linuxParameters        = { initProcessEnabled = true }
    mountPoints            = [{ sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }]
    volumesFrom            = []
    logConfiguration = {
      logDriver = "awslogs"
      options   = merge(local.log_options, { "awslogs-group" = aws_cloudwatch_log_group.services["api"].name })
    }
  }])
}

resource "aws_ecs_task_definition" "web" {
  family                   = "${var.project}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.web_task.arn
  volume { name = "web-cache" }
  container_definitions = jsonencode([{
    name         = "web"
    image        = "${aws_ecr_repository.images["web"].repository_url}:${var.image_tag}"
    essential    = true
    portMappings = [{ containerPort = 3000, protocol = "tcp" }]
    environment = [
      { name = "API_BASE_URL", value = "http://api.${var.project}.internal:8000" },
      { name = "OIDC_REDIRECT_URI", value = "https://${var.hostname}/callback" },
    ]
    secrets = [
      { name = "OIDC_ISSUER_URL", valueFrom = "${aws_secretsmanager_secret.application["oidc"].arn}:ISSUER_URL::" },
      { name = "OIDC_INTERNAL_ISSUER_URL", valueFrom = "${aws_secretsmanager_secret.application["oidc"].arn}:INTERNAL_ISSUER_URL::" },
      { name = "OIDC_CLIENT_ID", valueFrom = "${aws_secretsmanager_secret.application["oidc"].arn}:CLIENT_ID::" },
      { name = "OIDC_CLIENT_SECRET", valueFrom = "${aws_secretsmanager_secret.application["oidc"].arn}:CLIENT_SECRET::" },
      { name = "AUTH_SESSION_SECRET", valueFrom = "${aws_secretsmanager_secret.application["oidc"].arn}:SESSION_SECRET::" },
    ]
    readonlyRootFilesystem = true
    linuxParameters        = { initProcessEnabled = true }
    mountPoints            = [{ sourceVolume = "web-cache", containerPath = "/app/.next/cache", readOnly = false }]
    volumesFrom            = []
    logConfiguration = {
      logDriver = "awslogs"
      options   = merge(local.log_options, { "awslogs-group" = aws_cloudwatch_log_group.services["web"].name })
    }
  }])
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.project}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn
  volume { name = "tmp" }
  container_definitions = jsonencode([{
    name      = "worker"
    image     = "${aws_ecr_repository.images["scheduler"].repository_url}:${var.image_tag}"
    essential = true
    command   = ["python", "-m", "services.ingestion.orchestration.cli", "run-worker", "--poll-interval-seconds", "5"]
    environment = concat(local.common_environment, [
      { name = "CLAMD_HOST", value = "clamav.${var.project}.internal" },
    ])
    secrets                = concat(local.python_secrets, local.delivery_secrets)
    readonlyRootFilesystem = true
    linuxParameters        = { initProcessEnabled = true }
    mountPoints            = [{ sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }]
    volumesFrom            = []
    logConfiguration = {
      logDriver = "awslogs"
      options   = merge(local.log_options, { "awslogs-group" = aws_cloudwatch_log_group.services["worker"].name })
    }
  }])
}

resource "aws_ecs_task_definition" "scheduler" {
  family                   = "${var.project}-scheduler"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn
  volume { name = "tmp" }
  container_definitions = jsonencode([{
    name      = "scheduler"
    image     = "${aws_ecr_repository.images["scheduler"].repository_url}:${var.image_tag}"
    essential = true
    command   = ["python", "-m", "services.ingestion.orchestration.cli", "run-once"]
    environment = concat(local.common_environment, [
      { name = "CLAMD_HOST", value = "clamav.${var.project}.internal" },
      { name = "PROCINTEL_CLOUDWATCH_NAMESPACE", value = "Procintel" },
    ])
    secrets                = concat(local.python_secrets, local.provider_secrets, local.delivery_secrets)
    readonlyRootFilesystem = true
    linuxParameters        = { initProcessEnabled = true }
    mountPoints            = [{ sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }]
    volumesFrom            = []
    logConfiguration = {
      logDriver = "awslogs"
      options   = merge(local.log_options, { "awslogs-group" = aws_cloudwatch_log_group.services["scheduler"].name })
    }
  }])
}

resource "aws_ecs_task_definition" "migration" {
  family                   = "${var.project}-migration"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn
  volume { name = "tmp" }
  container_definitions = jsonencode([{
    name      = "migration"
    image     = "${aws_ecr_repository.images["scheduler"].repository_url}:${var.image_tag}"
    essential = true
    command   = ["bash", "/app/db/run_migrations_tracked.sh"]
    secrets = [
      { name = "DATABASE_URL", valueFrom = "${aws_secretsmanager_secret.application["app-database"].arn}:OWNER_DATABASE_URL::" },
      { name = "APP_DB_PASSWORD", valueFrom = "${aws_secretsmanager_secret.application["app-database"].arn}:APP_DB_PASSWORD::" },
    ]
    readonlyRootFilesystem = true
    linuxParameters        = { initProcessEnabled = true }
    mountPoints            = [{ sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }]
    volumesFrom            = []
    logConfiguration = {
      logDriver = "awslogs"
      options   = merge(local.log_options, { "awslogs-group" = aws_cloudwatch_log_group.services["migration"].name })
    }
  }])
}

resource "aws_ecs_task_definition" "clamav" {
  family                   = "${var.project}-clamav"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 3072
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn
  ephemeral_storage { size_in_gib = 30 }
  container_definitions = jsonencode([{
    name            = "clamav"
    image           = "clamav/clamav:1.4"
    essential       = true
    portMappings    = [{ containerPort = 3310, protocol = "tcp" }]
    linuxParameters = { initProcessEnabled = true }
    mountPoints     = []
    volumesFrom     = []
    logConfiguration = {
      logDriver = "awslogs"
      options   = merge(local.log_options, { "awslogs-group" = aws_cloudwatch_log_group.services["clamav"].name })
    }
  }])
}

# Continuously running services

resource "aws_ecs_service" "clamav" {
  name            = "clamav"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.clamav.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = local.private_subnets
    security_groups  = [aws_security_group.clamav.id]
    assign_public_ip = false
  }
  service_registries { registry_arn = aws_service_discovery_service.clamav.arn }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
}

resource "aws_ecs_service" "api" {
  name                               = "api"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.api.arn
  desired_count                      = var.api_desired_count
  launch_type                        = "FARGATE"
  health_check_grace_period_seconds  = 60
  enable_execute_command             = true
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  network_configuration {
    subnets          = local.private_subnets
    security_groups  = [aws_security_group.api.id]
    assign_public_ip = false
  }
  service_registries { registry_arn = aws_service_discovery_service.api.arn }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  depends_on = [aws_ecs_service.clamav]
}

resource "aws_ecs_service" "web" {
  name                               = "web"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.web.arn
  desired_count                      = var.web_desired_count
  launch_type                        = "FARGATE"
  health_check_grace_period_seconds  = 60
  enable_execute_command             = true
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  network_configuration {
    subnets          = local.private_subnets
    security_groups  = [aws_security_group.web.id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 3000
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  depends_on = [aws_lb_listener.https, aws_ecs_service.api]
}

resource "aws_ecs_service" "worker" {
  name                               = "worker"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.worker.arn
  desired_count                      = var.worker_desired_count
  launch_type                        = "FARGATE"
  enable_execute_command             = true
  deployment_minimum_healthy_percent = 50
  deployment_maximum_percent         = 200
  network_configuration {
    subnets          = local.private_subnets
    security_groups  = [aws_security_group.background.id]
    assign_public_ip = false
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  depends_on = [aws_ecs_service.clamav]
}

# API/web autoscaling

resource "aws_appautoscaling_target" "api" {
  max_capacity       = var.api_max_capacity
  min_capacity       = var.api_min_capacity
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.api.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "api_cpu" {
  name               = "${var.project}-api-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.api.resource_id
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension
  service_namespace  = aws_appautoscaling_target.api.service_namespace
  target_tracking_scaling_policy_configuration {
    predefined_metric_specification { predefined_metric_type = "ECSServiceAverageCPUUtilization" }
    target_value       = 60
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

resource "aws_appautoscaling_target" "web" {
  max_capacity       = var.web_max_capacity
  min_capacity       = var.web_min_capacity
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.web.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "web_requests" {
  name               = "${var.project}-web-requests"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.web.resource_id
  scalable_dimension = aws_appautoscaling_target.web.scalable_dimension
  service_namespace  = aws_appautoscaling_target.web.service_namespace
  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label         = "${aws_lb.web.arn_suffix}/${aws_lb_target_group.web.arn_suffix}"
    }
    target_value       = 1000
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

# EventBridge Scheduler starts one idempotent ingestion pass every day. There
# is no sleeping singleton process to miss a run after host suspension.

resource "aws_iam_role" "scheduler" {
  name = "${var.project}-event-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  name = "run-ecs-task"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "ecs:RunTask"
        Resource = aws_ecs_task_definition.scheduler.arn
      },
      {
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = [aws_iam_role.ecs_execution.arn, aws_iam_role.ecs_task.arn]
      }
    ]
  })
}

resource "aws_scheduler_schedule_group" "main" {
  name = var.project
}

resource "aws_scheduler_schedule" "daily_ingestion" {
  name                         = "${var.project}-daily-ingestion"
  group_name                   = aws_scheduler_schedule_group.main.name
  schedule_expression          = var.daily_ingestion_schedule
  schedule_expression_timezone = "Europe/Athens"
  state                        = "ENABLED"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_ecs_cluster.main.arn
    role_arn = aws_iam_role.scheduler.arn
    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 2
    }
    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.scheduler.arn
      launch_type         = "FARGATE"
      task_count          = 1
      network_configuration {
        subnets          = local.private_subnets
        security_groups  = [aws_security_group.background.id]
        assign_public_ip = false
      }
    }
  }
}

resource "aws_scheduler_schedule" "operational_metrics" {
  name                = "${var.project}-operational-metrics"
  group_name          = aws_scheduler_schedule_group.main.name
  schedule_expression = "rate(5 minutes)"
  state               = "ENABLED"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_ecs_cluster.main.arn
    role_arn = aws_iam_role.scheduler.arn
    input = jsonencode({
      containerOverrides = [{
        name    = "scheduler"
        command = ["python", "scripts/publish_operational_metrics.py"]
      }]
    })
    retry_policy {
      maximum_event_age_in_seconds = 900
      maximum_retry_attempts       = 2
    }
    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.scheduler.arn
      launch_type         = "FARGATE"
      task_count          = 1
      network_configuration {
        subnets          = local.private_subnets
        security_groups  = [aws_security_group.background.id]
        assign_public_ip = false
      }
    }
  }
}

# Edge abuse protection. The application also enforces credential + IP limits
# through the shared database limiter.

resource "aws_wafv2_web_acl" "web" {
  name  = var.project
  scope = "REGIONAL"
  default_action {
    allow {}
  }
  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.project}-waf"
    sampled_requests_enabled   = true
  }
  rule {
    name     = "ip-rate-limit"
    priority = 1
    action {
      block {}
    }
    statement {
      rate_based_statement {
        aggregate_key_type = "IP"
        limit              = var.waf_ip_rate_limit
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project}-ip-rate-limit"
      sampled_requests_enabled   = true
    }
  }
  rule {
    name     = "aws-common-rules"
    priority = 2
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project}-common-rules"
      sampled_requests_enabled   = true
    }
  }
}

resource "aws_wafv2_web_acl_association" "web" {
  resource_arn = aws_lb.web.arn
  web_acl_arn  = aws_wafv2_web_acl.web.arn
}

# Runtime alarms

resource "aws_cloudwatch_metric_alarm" "web_unhealthy" {
  alarm_name          = "${var.project}-web-unhealthy"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "breaching"
  dimensions = {
    LoadBalancer = aws_lb.web.arn_suffix
    TargetGroup  = aws_lb_target_group.web.arn_suffix
  }
  alarm_actions = [aws_sns_topic.operations.arn]
  ok_actions    = [aws_sns_topic.operations.arn]
}

resource "aws_cloudwatch_metric_alarm" "web_5xx" {
  alarm_name          = "${var.project}-web-5xx"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 2
  comparison_operator = "GreaterThanThreshold"
  threshold           = 10
  treat_missing_data  = "notBreaching"
  dimensions          = { LoadBalancer = aws_lb.web.arn_suffix }
  alarm_actions       = [aws_sns_topic.operations.arn]
  ok_actions          = [aws_sns_topic.operations.arn]
}

resource "aws_cloudwatch_metric_alarm" "ecs_cpu" {
  for_each            = toset(["api", "web", "worker"])
  alarm_name          = "${var.project}-${each.value}-cpu"
  namespace           = "AWS/ECS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  comparison_operator = "GreaterThanThreshold"
  threshold           = 85
  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = each.value
  }
  alarm_actions = [aws_sns_topic.operations.arn]
}

resource "aws_cloudwatch_metric_alarm" "source_stale" {
  for_each            = toset(["KHMDHS", "DIAVGEIA", "GEMI", "TED"])
  alarm_name          = "${var.project}-${lower(each.value)}-stale"
  namespace           = "Procintel"
  metric_name         = "SourceFreshnessSeconds"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 3
  comparison_operator = "GreaterThanThreshold"
  threshold           = 172800
  treat_missing_data  = "breaching"
  dimensions          = { Source = each.value }
  alarm_actions       = [aws_sns_topic.operations.arn]
  ok_actions          = [aws_sns_topic.operations.arn]
}

resource "aws_cloudwatch_metric_alarm" "ingestion_stale" {
  alarm_name          = "${var.project}-daily-ingestion-stale"
  namespace           = "Procintel"
  metric_name         = "LastSuccessfulIngestionAgeSeconds"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 3
  comparison_operator = "GreaterThanThreshold"
  threshold           = 129600
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.operations.arn]
  ok_actions          = [aws_sns_topic.operations.arn]
}

resource "aws_cloudwatch_metric_alarm" "dead_jobs" {
  alarm_name          = "${var.project}-dead-enrichment-jobs"
  namespace           = "Procintel"
  metric_name         = "EnrichmentDeadJobs"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.operations.arn]
  ok_actions          = [aws_sns_topic.operations.arn]
}

resource "aws_cloudwatch_metric_alarm" "durable_job_stale" {
  alarm_name          = "${var.project}-durable-job-stale"
  namespace           = "Procintel"
  metric_name         = "OldestDurableJobAgeSeconds"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 3
  comparison_operator = "GreaterThanThreshold"
  threshold           = 1800
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.operations.arn]
  ok_actions          = [aws_sns_topic.operations.arn]
}

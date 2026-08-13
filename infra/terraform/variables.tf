variable "project" {
  type    = string
  default = "procintel"
}

variable "aws_region" {
  type    = string
  default = "eu-central-1"
}

variable "vpc_cidr" {
  type    = string
  default = "10.42.0.0/16"
}

variable "database_name" {
  type    = string
  default = "procintel"
}

variable "database_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

variable "opensearch_instance_type" {
  type    = string
  default = "t3.small.search"
}

variable "alert_email" {
  type        = string
  description = "Optional operations email for CloudWatch/SNS alerts."
  default     = ""
}

variable "environment" {
  type    = string
  default = "production"
}

variable "hostname" {
  type        = string
  description = "Public application hostname, for example app.procintel.gr."
}

variable "hosted_zone_id" {
  type        = string
  description = "Optional Route53 hosted-zone ID. Leave empty when DNS is managed elsewhere."
  default     = ""
}

variable "certificate_arn" {
  type        = string
  description = "ACM certificate ARN for hostname."
}

variable "image_tag" {
  type        = string
  description = "Immutable release tag deployed to every Procintel image."
  default     = "release"
}

variable "api_desired_count" {
  type    = number
  default = 2
}

variable "web_desired_count" {
  type    = number
  default = 2
}

variable "worker_desired_count" {
  type    = number
  default = 2
}

variable "api_min_capacity" {
  type    = number
  default = 2
}

variable "api_max_capacity" {
  type    = number
  default = 8
}

variable "web_min_capacity" {
  type    = number
  default = 2
}

variable "web_max_capacity" {
  type    = number
  default = 8
}

variable "api_rate_limit_per_minute" {
  type    = number
  default = 600
}

variable "waf_ip_rate_limit" {
  type        = number
  description = "Maximum requests from one IP in a five-minute WAF window."
  default     = 3000
}

variable "daily_ingestion_schedule" {
  type    = string
  default = "cron(30 2 * * ? *)"
}

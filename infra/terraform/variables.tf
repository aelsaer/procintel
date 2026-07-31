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

variable "project_name" {
  description = "The name of the project"
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "List of availability zones"
  type        = list(string)
  default     = ["ap-northeast-2a", "ap-northeast-2c"]
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "ap-northeast-2"
}

# Subnet CIDR blocks
variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets (ALB, NAT Gateway)"
  type        = list(string)
  default     = ["10.0.0.0/26", "10.0.0.64/26"]
}

variable "network_subnet_cidrs" {
  description = "CIDR blocks for network subnets (VPC Endpoints)"
  type        = list(string)
  default     = ["10.0.0.128/26", "10.0.0.192/26"]
}

variable "app_subnet_cidrs" {
  description = "CIDR blocks for app subnets (ECS Cluster)"
  type        = list(string)
  default     = ["10.0.1.0/25", "10.0.1.128/25"]
}

variable "data_subnet_cidrs" {
  description = "CIDR blocks for data subnets (OpenSearch)"
  type        = list(string)
  default     = ["10.0.2.0/26", "10.0.2.64/26"]
}

variable "lambda_subnet_cidrs" {
  description = "CIDR blocks for Lambda subnets"
  type        = list(string)
  default     = ["10.0.32.0/19", "10.0.64.0/19"]
}

variable "enable_interface_endpoints" {
  description = "Enable VPC Interface Endpoints (ECR, ECS, CloudWatch, etc.)"
  type        = bool
  default     = true
}

variable "project_name" {
  description = "Project name for resource naming"
  type        = string
  default     = "s7c-shawn-personal-an2"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "personal"
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "ap-northeast-2"
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

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets"
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

# VPC Endpoints
variable "enable_interface_endpoints" {
  description = "Enable VPC Interface Endpoints (disable for cost savings)"
  type        = bool
  default     = false
}

# EC2 Variables
variable "ec2_instance_type" {
  description = "EC2 instance type for Slack Bot"
  type        = string
  default     = "m6i.large"
}

variable "ec2_key_name" {
  description = "SSH key pair name for EC2"
  type        = string
  default     = "slack-bot-key"
}

variable "ec2_create_key_pair" {
  description = "Whether to create a new key pair"
  type        = bool
  default     = true
}

variable "ec2_allowed_ssh_cidr_blocks" {
  description = "CIDR blocks allowed for SSH access"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "ec2_root_volume_size" {
  description = "Root EBS volume size in GB"
  type        = number
  default     = 100
}

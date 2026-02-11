# VPC Outputs
output "vpc_id" {
  description = "The ID of the VPC"
  value       = module.vpc.vpc_id
}

output "vpc_cidr" {
  description = "The CIDR block of the VPC"
  value       = module.vpc.vpc_cidr
}

# Subnet IDs
output "public_subnet_ids" {
  description = "List of IDs of public subnets"
  value       = module.vpc.public_subnet_ids
}

output "network_subnet_ids" {
  description = "List of IDs of network subnets"
  value       = module.vpc.network_subnet_ids
}

output "app_subnet_ids" {
  description = "List of IDs of app subnets"
  value       = module.vpc.app_subnet_ids
}

output "data_subnet_ids" {
  description = "List of IDs of data subnets"
  value       = module.vpc.data_subnet_ids
}

output "lambda_subnet_ids" {
  description = "List of IDs of Lambda subnets"
  value       = module.vpc.lambda_subnet_ids
}

# Security Group IDs
output "endpoint_security_group_id" {
  description = "Security group ID for VPC endpoints"
  value       = module.vpc.endpoint_security_group_id
}

output "ecs_task_security_group_id" {
  description = "Security group ID for ECS tasks"
  value       = module.vpc.ecs_task_security_group_id
}

output "opensearch_security_group_id" {
  description = "Security group ID for OpenSearch domain"
  value       = module.vpc.opensearch_security_group_id
}

output "lambda_security_group_id" {
  description = "Security group ID for Lambda functions"
  value       = module.vpc.lambda_security_group_id
}

# NAT Gateway
output "nat_gateway_ids" {
  description = "List of NAT Gateway IDs"
  value       = module.vpc.nat_gateway_ids
}

output "nat_gateway_public_ips" {
  description = "List of NAT Gateway public IPs"
  value       = module.vpc.nat_gateway_public_ips
}

# EC2 Outputs
output "ec2_instance_id" {
  description = "EC2 instance ID"
  value       = module.ec2_slack_bot.instance_id
}

output "ec2_public_ip" {
  description = "EC2 public IP address"
  value       = module.ec2_slack_bot.instance_public_ip
}

output "ec2_private_ip" {
  description = "EC2 private IP address"
  value       = module.ec2_slack_bot.instance_private_ip
}

output "ec2_ssh_command" {
  description = "SSH command to connect"
  value       = module.ec2_slack_bot.ssh_command
}

# S3 Vectors Outputs
output "gov_funding_snapshots_vector_bucket" {
  description = "Gov Funding Monitor S3 Vectors bucket name"
  value       = aws_s3vectors_vector_bucket.gov_funding_snapshots.vector_bucket_name
}

output "gov_funding_snapshots_vector_bucket_arn" {
  description = "Gov Funding Monitor S3 Vectors bucket ARN"
  value       = aws_s3vectors_vector_bucket.gov_funding_snapshots.vector_bucket_arn
}

output "s3vectors_ai_news_arn" {
  description = "ARN of the AI News S3 Vectors bucket"
  value       = module.s3vectors_ai_news.vector_bucket_arn
}

output "s3vectors_ai_news_name" {
  description = "Name of the AI News S3 Vectors bucket"
  value       = module.s3vectors_ai_news.vector_bucket_name
}

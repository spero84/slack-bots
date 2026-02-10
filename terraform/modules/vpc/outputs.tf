output "vpc_id" {
  description = "The ID of the VPC"
  value       = aws_vpc.main.id
}

output "vpc_cidr" {
  description = "The CIDR block of the VPC"
  value       = aws_vpc.main.cidr_block
}

# Subnet IDs
output "public_subnet_ids" {
  description = "List of IDs of public subnets"
  value       = aws_subnet.public[*].id
}

output "network_subnet_ids" {
  description = "List of IDs of network subnets (VPC Endpoints)"
  value       = aws_subnet.network[*].id
}

output "app_subnet_ids" {
  description = "List of IDs of app subnets (ECS Cluster)"
  value       = aws_subnet.app[*].id
}

output "data_subnet_ids" {
  description = "List of IDs of data subnets (OpenSearch)"
  value       = aws_subnet.data[*].id
}

output "lambda_subnet_ids" {
  description = "List of IDs of Lambda subnets"
  value       = aws_subnet.lambda[*].id
}

# Security Group IDs
output "endpoint_security_group_id" {
  description = "Security group ID for VPC endpoints"
  value       = aws_security_group.endpoint.id
}

output "ecs_task_security_group_id" {
  description = "Security group ID for ECS tasks"
  value       = aws_security_group.ecs_task.id
}

output "opensearch_security_group_id" {
  description = "Security group ID for OpenSearch domain"
  value       = aws_security_group.opensearch.id
}

output "lambda_security_group_id" {
  description = "Security group ID for Lambda functions"
  value       = aws_security_group.lambda.id
}

# NAT Gateway
output "nat_gateway_ids" {
  description = "List of NAT Gateway IDs"
  value       = aws_nat_gateway.main[*].id
}

output "nat_gateway_public_ips" {
  description = "List of NAT Gateway public IPs"
  value       = aws_eip.nat[*].public_ip
}

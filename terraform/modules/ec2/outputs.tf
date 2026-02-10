output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.slack_bot.id
}

output "instance_public_ip" {
  description = "EC2 instance public IP"
  value       = aws_instance.slack_bot.public_ip
}

output "instance_private_ip" {
  description = "EC2 instance private IP"
  value       = aws_instance.slack_bot.private_ip
}

output "security_group_id" {
  description = "Security group ID"
  value       = aws_security_group.slack_bot.id
}

output "iam_role_arn" {
  description = "IAM role ARN"
  value       = aws_iam_role.slack_bot.arn
}

output "ssh_command" {
  description = "SSH command to connect to the instance"
  value       = var.create_key_pair ? "ssh -i ${var.key_name}.pem ubuntu@${aws_instance.slack_bot.public_ip}" : "ssh -i ~/.ssh/${var.key_name}.pem ubuntu@${aws_instance.slack_bot.public_ip}"
}

output "private_key_path" {
  description = "Path to private key file (if created)"
  value       = var.create_key_pair ? "${path.root}/${var.key_name}.pem" : null
}

output "private_key_pem" {
  description = "Private key PEM content (sensitive)"
  value       = var.create_key_pair ? tls_private_key.slack_bot[0].private_key_pem : null
  sensitive   = true
}


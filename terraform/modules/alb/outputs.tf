# All ALB ARNs as a map
output "alb_arns" {
  description = "Map of ALB names to ARNs"
  value       = { for k, v in aws_lb.main : k => v.arn }
}

# All ALB DNS names as a map
output "alb_dns_names" {
  description = "Map of ALB names to DNS names"
  value       = { for k, v in aws_lb.main : k => v.dns_name }
}

# All ALB zone IDs as a map
output "alb_zone_ids" {
  description = "Map of ALB names to zone IDs"
  value       = { for k, v in aws_lb.main : k => v.zone_id }
}

# All ALB security group IDs as a map
output "alb_security_group_ids" {
  description = "Map of ALB names to security group IDs"
  value       = { for k, v in aws_security_group.alb : k => v.id }
}

# All API target group ARNs as a map
output "api_target_group_arns" {
  description = "Map of ALB names to API target group ARNs"
  value       = { for k, v in aws_lb_target_group.api : k => v.arn }
}

# All UI target group ARNs as a map
output "ui_target_group_arns" {
  description = "Map of ALB names to UI target group ARNs"
  value       = { for k, v in aws_lb_target_group.ui : k => v.arn }
}

# All HTTPS listener ARNs as a map
output "https_listener_arns" {
  description = "Map of ALB names to HTTPS listener ARNs"
  value       = { for k, v in aws_lb_listener.https : k => v.arn }
}

# Individual outputs for ESS (backward compatibility)
output "ess_alb_arn" {
  description = "ARN of the ESS Application Load Balancer"
  value       = try(aws_lb.main["ess"].arn, null)
}

output "ess_alb_dns_name" {
  description = "DNS name of the ESS Application Load Balancer"
  value       = try(aws_lb.main["ess"].dns_name, null)
}

output "ess_alb_security_group_id" {
  description = "Security group ID of the ESS ALB"
  value       = try(aws_security_group.alb["ess"].id, null)
}

output "ess_api_target_group_arn" {
  description = "ARN of the ESS API target group"
  value       = try(aws_lb_target_group.api["ess"].arn, null)
}

output "ess_ui_target_group_arn" {
  description = "ARN of the ESS UI target group"
  value       = try(aws_lb_target_group.ui["ess"].arn, null)
}

# Individual outputs for Console
output "console_alb_arn" {
  description = "ARN of the Console Application Load Balancer"
  value       = try(aws_lb.main["console"].arn, null)
}

output "console_alb_dns_name" {
  description = "DNS name of the Console Application Load Balancer"
  value       = try(aws_lb.main["console"].dns_name, null)
}

output "console_alb_security_group_id" {
  description = "Security group ID of the Console ALB"
  value       = try(aws_security_group.alb["console"].id, null)
}

output "console_api_target_group_arn" {
  description = "ARN of the Console API target group"
  value       = try(aws_lb_target_group.api["console"].arn, null)
}

output "console_ui_target_group_arn" {
  description = "ARN of the Console UI target group"
  value       = try(aws_lb_target_group.ui["console"].arn, null)
}

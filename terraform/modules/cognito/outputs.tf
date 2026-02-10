# All user pool IDs as a map
output "user_pool_ids" {
  description = "Map of pool names to User Pool IDs"
  value       = { for k, v in aws_cognito_user_pool.main : k => v.id }
}

# All user pool ARNs as a map
output "user_pool_arns" {
  description = "Map of pool names to User Pool ARNs"
  value       = { for k, v in aws_cognito_user_pool.main : k => v.arn }
}

# All client IDs as a map
output "user_pool_client_ids" {
  description = "Map of pool names to User Pool Client IDs"
  value       = { for k, v in aws_cognito_user_pool_client.main : k => v.id }
}

# All client secrets as a map (sensitive)
output "user_pool_client_secrets" {
  description = "Map of pool names to User Pool Client Secrets"
  value       = { for k, v in aws_cognito_user_pool_client.main : k => v.client_secret }
  sensitive   = true
}

# All domains as a map
output "cognito_domains" {
  description = "Map of pool names to Cognito Domains"
  value       = { for k, v in aws_cognito_user_pool_domain.main : k => v.domain }
}

# Individual outputs for ESS (backward compatibility)
output "ess_user_pool_id" {
  description = "The ID of the ESS Cognito User Pool"
  value       = try(aws_cognito_user_pool.main["ess"].id, null)
}

output "ess_user_pool_arn" {
  description = "The ARN of the ESS Cognito User Pool"
  value       = try(aws_cognito_user_pool.main["ess"].arn, null)
}

output "ess_user_pool_client_id" {
  description = "The ID of the ESS Cognito User Pool Client"
  value       = try(aws_cognito_user_pool_client.main["ess"].id, null)
}

output "ess_user_pool_client_secret" {
  description = "The secret of the ESS Cognito User Pool Client"
  value       = try(aws_cognito_user_pool_client.main["ess"].client_secret, null)
  sensitive   = true
}

# Individual outputs for Console
output "console_user_pool_id" {
  description = "The ID of the Console Cognito User Pool"
  value       = try(aws_cognito_user_pool.main["console"].id, null)
}

output "console_user_pool_arn" {
  description = "The ARN of the Console Cognito User Pool"
  value       = try(aws_cognito_user_pool.main["console"].arn, null)
}

output "console_user_pool_client_id" {
  description = "The ID of the Console Cognito User Pool Client"
  value       = try(aws_cognito_user_pool_client.main["console"].id, null)
}

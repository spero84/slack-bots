# State migration: Move existing resources to new for_each keys
moved {
  from = aws_cognito_user_pool.main
  to   = aws_cognito_user_pool.main["ess"]
}

moved {
  from = aws_cognito_user_pool_client.main
  to   = aws_cognito_user_pool_client.main["ess"]
}

moved {
  from = aws_cognito_user_pool_domain.main
  to   = aws_cognito_user_pool_domain.main["ess"]
}

# Import blocks for existing Console Cognito resources
import {
  for_each = { for pool in var.user_pools : pool.name => pool if pool.name == "console" }
  to       = aws_cognito_user_pool_domain.main[each.key]
  id       = each.value.domain_prefix
}

# Cognito User Pools for all services (ESS + Console)
resource "aws_cognito_user_pool" "main" {
  for_each = { for pool in var.user_pools : pool.name => pool }

  name = "${var.project_name}-${each.key}-user-pool"

  # Sign-up settings
  auto_verified_attributes = ["email"]

  # Username attributes
  username_attributes = ["email"]

  # Password policy (varies by client type)
  password_policy {
    minimum_length    = each.value.password_min
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = each.value.require_symbols
  }

  # Email configuration
  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }

  # Schema for required attributes
  schema {
    name                     = "email"
    attribute_data_type      = "String"
    required                 = true
    mutable                  = true
    developer_only_attribute = false

    string_attribute_constraints {
      min_length = 0
      max_length = 2048
    }
  }

  # Account recovery
  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${each.key}-user-pool"
  })
}

# Cognito User Pool Clients
resource "aws_cognito_user_pool_client" "main" {
  for_each = { for pool in var.user_pools : pool.name => pool }

  name         = "${var.project_name}-${each.key}-client"
  user_pool_id = aws_cognito_user_pool.main[each.key].id

  # Server-side (confidential) vs browser-based (public) clients
  generate_secret = each.value.client_type == "confidential" ? true : false

  # OAuth settings
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["email", "openid", "profile"]

  callback_urls = each.value.callback_urls
  logout_urls   = each.value.logout_urls

  supported_identity_providers = ["COGNITO"]

  # Auth flows (more for confidential, fewer for public)
  explicit_auth_flows = each.value.client_type == "confidential" ? [
    "ALLOW_ADMIN_USER_PASSWORD_AUTH",
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_CUSTOM_AUTH",
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH"
    ] : [
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH"
  ]

  prevent_user_existence_errors = "ENABLED"

  # Token validity
  refresh_token_validity = 30
  access_token_validity  = 1
  id_token_validity      = 1

  token_validity_units {
    refresh_token = "days"
    access_token  = "hours"
    id_token      = "hours"
  }

  # Read/write attributes
  read_attributes  = ["email", "email_verified"]
  write_attributes = ["email"]
}

# Cognito User Pool Domains
resource "aws_cognito_user_pool_domain" "main" {
  for_each = { for pool in var.user_pools : pool.name => pool }

  domain       = each.value.domain_prefix
  user_pool_id = aws_cognito_user_pool.main[each.key].id
}

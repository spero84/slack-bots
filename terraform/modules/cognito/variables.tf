variable "project_name" {
  description = "The name of the project"
  type        = string
}

variable "user_pools" {
  description = "List of user pools to create"
  type = list(object({
    name            = string       # "ess" or "console"
    domain_prefix   = string       # Cognito domain prefix
    client_type     = string       # "confidential" (server) or "public" (browser)
    password_min    = number       # Minimum password length (12 for ess, 8 for console)
    require_symbols = bool         # Whether to require symbols in password
    callback_urls   = list(string) # OAuth callback URLs
    logout_urls     = list(string) # OAuth logout URLs
  }))
  default = []
}

variable "tags" {
  description = "Additional tags for resources"
  type        = map(string)
  default     = {}
}

variable "project_name" {
  description = "The name of the project (used for resource naming)"
  type        = string
}

variable "vpc_id" {
  description = "The ID of the VPC"
  type        = string
}

variable "subnet_ids" {
  description = "List of subnet IDs for ALB"
  type        = list(string)
}

variable "ecs_security_group_id" {
  description = "Security group ID of ECS tasks (for adding ingress rule)"
  type        = string
}

variable "certificate_arn" {
  description = "ARN of the ACM certificate for HTTPS listener"
  type        = string
}

variable "load_balancers" {
  description = "List of load balancers to create"
  type = list(object({
    name                = string       # "ess" or "console"
    internal            = bool         # true for internal, false for internet-facing
    allowed_cidr_blocks = list(string) # CIDR blocks allowed to access
    enable_websocket    = bool         # true for ESS (WebSocket support)
    enable_route53      = bool         # true to create Route53 record
    domain              = string       # Domain for Route53 record
    api_container_port  = number       # API container port
    ui_container_port   = number       # UI container port
    api_health_check    = string       # API health check path
    ui_health_check     = string       # UI health check path
  }))
  default = []
}

variable "route53_zone_id" {
  description = "Route 53 Hosted Zone ID for ALB records"
  type        = string
  default     = ""
}

variable "enable_deletion_protection" {
  description = "Enable deletion protection for ALB"
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}

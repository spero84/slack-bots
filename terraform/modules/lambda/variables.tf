variable "environment" {
  description = "Environment name (poc, dev, staging, prod)"
  type        = string
}

variable "stack_name" {
  description = "Stack name for resource naming"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID for Lambda functions"
  type        = string
}

variable "subnet_ids" {
  description = "Subnet IDs for Lambda functions"
  type        = list(string)
}

variable "data_bucket_name" {
  description = "S3 bucket for data storage"
  type        = string
}

variable "secret_name" {
  description = "Secrets Manager secret name for OpenSearch credentials"
  type        = string
}

variable "bedrock_region" {
  description = "Region for Bedrock API access"
  type        = string
}

variable "embedding_dimension" {
  description = "Dimension for embeddings"
  type        = number
  default     = 1024
}

variable "bedrock_secret_key" {
  description = "Secret key for Bedrock access"
  type        = string
}

variable "bedrock_assume_id" {
  description = "AWS Account ID to assume role for Bedrock access"
  type        = string
}

variable "app_bucket_name" {
  description = "S3 bucket name in AIApp account"
  type        = string
}

variable "assume_role_arn_in_aiapp" {
  description = "IAM Role ARN to assume in AIApp account"
  type        = string
}

variable "synonyms" {
  description = "Path to synonyms file"
  type        = string
  default     = ""
}

variable "compound_nouns" {
  description = "Path to compound nouns file"
  type        = string
  default     = ""
}

variable "lambda_source_path" {
  description = "Base path for Lambda function source code"
  type        = string
  default     = "../../../../../indexing/src"
}

variable "common_layer_path" {
  description = "Path to common layer source code"
  type        = string
  default     = "../../../../../indexing/src/layers/common_layer"
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
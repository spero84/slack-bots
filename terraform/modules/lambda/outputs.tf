output "security_group_id" {
  description = "Security group ID for Lambda functions"
  value       = aws_security_group.lambda.id
}

output "common_layer_arn" {
  description = "ARN of the common Lambda layer"
  value       = aws_lambda_layer_version.common.arn
}

# Lambda Function ARNs - Main Pipeline
output "entr2_function_arn" {
  description = "ARN of Document Listing (doc-list) Lambda function"
  value       = aws_lambda_function.entr2.arn
}

output "entr3_function_arn" {
  description = "ARN of Document Parsing (doc-parse) Lambda function"
  value       = aws_lambda_function.entr3.arn
}

output "entr4_function_arn" {
  description = "ARN of Document Chunking (chunk) Lambda function"
  value       = aws_lambda_function.entr4.arn
}

output "entr5_function_arn" {
  description = "ARN of Embedding & Indexing (embed-idx) Lambda function"
  value       = aws_lambda_function.entr5.arn
}

output "entr6_function_arn" {
  description = "ARN of Final Processing (final) Lambda function"
  value       = aws_lambda_function.entr6.arn
}

output "checkpoints_function_arn" {
  description = "ARN of Checkpoint Processing (checkpoint) Lambda function"
  value       = aws_lambda_function.checkpoints.arn
}

output "onerous_claude_function_arn" {
  description = "ARN of Risk Clause Analysis (risk-clause) Lambda function"
  value       = aws_lambda_function.onerous_claude.arn
}

# Lambda Function ARNs - Addendum Pipeline
output "addm_entr1_function_arn" {
  description = "ARN of Addendum File Analysis (addm-analyze) Lambda function"
  value       = aws_lambda_function.addm_entr1.arn
}

output "addm_index_copy_function_arn" {
  description = "ARN of Addendum Index Copy (addm-idx-copy) Lambda function"
  value       = aws_lambda_function.addm_index_copy.arn
}

output "addm_entr2_function_arn" {
  description = "ARN of Addendum Document Parsing (addm-parse) Lambda function"
  value       = aws_lambda_function.addm_entr2.arn
}

output "addm_entr3_function_arn" {
  description = "ARN of Addendum TOC Tree Chunking (addm-toc) Lambda function"
  value       = aws_lambda_function.addm_entr3.arn
}

output "addm_entr4_function_arn" {
  description = "ARN of Addendum OpenSearch Comparison (addm-compare) Lambda function"
  value       = aws_lambda_function.addm_entr4.arn
}

output "addm_entr5_function_arn" {
  description = "ARN of Addendum Embedding & Indexing (addm-embed) Lambda function"
  value       = aws_lambda_function.addm_entr5.arn
}

# Lambda Function Names
output "lambda_function_names" {
  description = "Map of Lambda function names"
  value = {
    entr2             = aws_lambda_function.entr2.function_name
    entr3             = aws_lambda_function.entr3.function_name
    entr4             = aws_lambda_function.entr4.function_name
    entr5             = aws_lambda_function.entr5.function_name
    entr6             = aws_lambda_function.entr6.function_name
    checkpoints       = aws_lambda_function.checkpoints.function_name
    onerous_claude    = aws_lambda_function.onerous_claude.function_name
    addm_entr1        = aws_lambda_function.addm_entr1.function_name
    addm_index_copy   = aws_lambda_function.addm_index_copy.function_name
    addm_entr2        = aws_lambda_function.addm_entr2.function_name
    addm_entr3        = aws_lambda_function.addm_entr3.function_name
    addm_entr4        = aws_lambda_function.addm_entr4.function_name
    addm_entr5        = aws_lambda_function.addm_entr5.function_name
  }
}

# IAM Role ARNs
output "sagemaker_execution_role_arn" {
  description = "ARN of SageMaker execution role"
  value       = aws_iam_role.sagemaker_execution.arn
}
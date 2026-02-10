output "task_role_arn" {
  description = "ARN of the ECS task role"
  value       = aws_iam_role.task_role.arn
}

output "task_role_name" {
  description = "Name of the ECS task role"
  value       = aws_iam_role.task_role.name
}

output "task_execution_role_arn" {
  description = "ARN of the ECS task execution role"
  value       = aws_iam_role.task_execution_role.arn
}

output "task_execution_role_name" {
  description = "Name of the ECS task execution role"
  value       = aws_iam_role.task_execution_role.name
}

output "codebuild_role_arn" {
  description = "ARN of the CodeBuild role"
  value       = aws_iam_role.codebuild_role.arn
}

output "codebuild_role_name" {
  description = "Name of the CodeBuild role"
  value       = aws_iam_role.codebuild_role.name
}

output "codedeploy_role_arn" {
  description = "ARN of the CodeDeploy role"
  value       = aws_iam_role.codedeploy_role.arn
}

output "codedeploy_role_name" {
  description = "Name of the CodeDeploy role"
  value       = aws_iam_role.codedeploy_role.name
}

output "codepipeline_role_arn" {
  description = "ARN of the CodePipeline role"
  value       = aws_iam_role.codepipeline_role.arn
}

output "codepipeline_role_name" {
  description = "Name of the CodePipeline role"
  value       = aws_iam_role.codepipeline_role.name
}

output "cognito_access_role_arn" {
  description = "ARN of the Cognito access role for ECS task to assume"
  value       = aws_iam_role.cognito_access_role.arn
}
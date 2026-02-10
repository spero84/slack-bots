# SAM to Terraform Migration Guide

## Overview

This guide documents the migration from AWS SAM template to Terraform/Terragrunt for the ITB Reviewer Indexing pipeline.

## Migration Components

### 1. Lambda Functions
**SAM Location**: `template.yaml` (AWS::Serverless::Function resources)
**Terraform Location**: `/modules/lambda/`

- Total Functions: 14
- Common Layer: Migrated to `aws_lambda_layer_version`
- VPC Configuration: Preserved with security group
- Environment Variables: Mapped to Terraform variables

### 2. Step Functions
**SAM Location**: `template.yaml` (AWS::StepFunctions::StateMachine resources)
**Terraform Location**: `/modules/step-functions/`

- State Machines: 8 total
- Definition Files: Stored in `/modules/step-functions/definitions/`
- IAM Roles: Created separately for each state machine

### 3. Security Groups
**SAM Location**: `LambdaSecurityGroup` resource
**Terraform Location**: `/modules/lambda/security_group.tf`

- Egress: HTTPS (443) only
- Ingress: None (default deny)

### 4. IAM Roles
**SAM Location**: Various role resources
**Terraform Location**:
- `/modules/lambda/iam_roles.tf` (Lambda execution roles)
- `/modules/step-functions/iam_roles.tf` (Step Functions execution roles)

## Deployment Steps

### Prerequisites
1. Ensure Lambda source code exists at `/Users/sunghyon/Projects/Searchdoc/infra-iac/indexing/src/`
2. AWS credentials configured
3. Terragrunt installed

### Deploy to POC Environment

```bash
# Navigate to POC environment
cd /Users/sunghyon/Projects/Searchdoc/infra-iac/aws-baseline/terraform/environments/poc

# Deploy VPC first (if not already deployed)
cd vpc
terragrunt apply

# Deploy Lambda functions
cd ../lambda
terragrunt apply

# Deploy Step Functions
cd ../step-functions
terragrunt apply
```

## Configuration Mapping

### SAM Parameters → Terraform Variables

| SAM Parameter | Terraform Variable | POC Value |
|--------------|-------------------|-----------|
| DataBucketName | data_bucket_name | itb-reviewer-poc-ap-northeast-2 |
| SecretName | secret_name | aws-poc/opensearch/credentials |
| BedrockRegion | bedrock_region | ap-northeast-2 |
| EmbeddingDimension | embedding_dimension | 1024 |
| VpcId | vpc_id | (from VPC module output) |
| SubnetIds | subnet_ids | (from VPC module output) |
| BedrockAssumeId | bedrock_assume_id | 913388732877 |

## Key Differences from SAM

1. **Module Structure**: Resources organized into reusable modules
2. **State Management**: Terragrunt handles remote state automatically
3. **Dependencies**: Explicit dependencies between modules via Terragrunt
4. **Variable Management**: Environment-specific values in terragrunt.hcl files
5. **Source Code Packaging**: Uses Terraform's `archive_file` data source

## Improvements Made

1. **Better Organization**: Separated Lambda and Step Functions into distinct modules
2. **DRY Principle**: Reusable modules for multiple environments
3. **Explicit Dependencies**: Clear dependency chain between resources
4. **Standardized Naming**: Consistent resource naming conventions
5. **Enhanced Security**: IAM roles follow least privilege principle

## Manual Steps Required

1. **S3 Buckets**: Ensure referenced S3 buckets exist:
   - itb-reviewer-poc-ap-northeast-2
   - ai-app-d-an2-s3-air

2. **Secrets Manager**: Create secret with OpenSearch credentials:
   - aws-poc/opensearch/credentials

3. **Cross-Account Roles**: Ensure these IAM roles exist in target accounts:
   - arn:aws:iam::543239041290:role/air-access-s3-from-itb-reviewer
   - arn:aws:iam::913388732877:role/ai-workloads-ou-role-bedrock-app

## Rollback Plan

If issues occur:
1. Keep SAM stack running in parallel initially
2. Test Terraform deployment thoroughly
3. Switch traffic gradually
4. Delete SAM stack only after successful validation

## Notes

- Lambda function code is referenced from the original SAM project location
- Step Functions definitions are templated to inject Lambda ARNs
- VPC configuration assumes existing VPC module deployment
- Some SAM features (like automatic CloudWatch Logs) are handled implicitly by Terraform
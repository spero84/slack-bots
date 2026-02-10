# Lambda Module

This module provisions Lambda functions and related resources for the ITB Reviewer Indexing pipeline.

## Resources Created

- **Lambda Functions** (13 total)
- **Lambda Layer**: Common layer for shared dependencies
- **Security Group**: VPC security group for Lambda functions (HTTPS egress only)
- **IAM Roles**: Execution roles with appropriate permissions

## Lambda Function Names

Lambda 함수 이름은 `{stack_name}-{identifier}-{role}` 형식을 따릅니다.

### Main Pipeline Functions

| Function Name | Identifier | Role Keyword | Description |
|--------------|------------|--------------|-------------|
| `{stack_name}-entr-2-doc-list` | entr-2 | doc-list | 문서 목록 조회 (Document Listing) |
| `{stack_name}-entr-3-doc-parse` | entr-3 | doc-parse | 문서 파싱 - Vision API (Document Parsing) |
| `{stack_name}-entr-4-chunk` | entr-4 | chunk | 문서 청킹 (Document Chunking) |
| `{stack_name}-entr-5-embed-idx` | entr-5 | embed-idx | 임베딩 & 인덱싱 (Embedding & Indexing) |
| `{stack_name}-entr-6-final` | entr-6 | final | 최종 처리 (Final Processing) |
| `{stack_name}-ckpt-checkpoint` | ckpt | checkpoint | 체크포인트 처리 (Checkpoint Processing) |
| `{stack_name}-oner-risk-clause` | oner | risk-clause | 위험 조항 분석 (Risk Clause Analysis) |

### Addendum Pipeline Functions

| Function Name | Identifier | Role Keyword | Description |
|--------------|------------|--------------|-------------|
| `{stack_name}-addm-0-idx-copy` | addm-0 | idx-copy | 인덱스 복사 (Index Copy) |
| `{stack_name}-addm-1-analyze` | addm-1 | analyze | 파일 분석 (File Analysis) |
| `{stack_name}-addm-2-parse` | addm-2 | parse | 문서 파싱 (Document Parsing) |
| `{stack_name}-addm-3-toc` | addm-3 | toc | TOC 트리 청킹 (TOC Tree Chunking) |
| `{stack_name}-addm-4-compare` | addm-4 | compare | OpenSearch 비교 (OpenSearch Comparison) |
| `{stack_name}-addm-5-embed` | addm-5 | embed | 임베딩 & 인덱싱 (Embedding & Indexing) |

### Example (dev04 environment)

```
stack_name = s7c-fiskr-poc-an2-lmd

s7c-fiskr-poc-an2-lmd-entr-2-doc-list
s7c-fiskr-poc-an2-lmd-entr-3-doc-parse
s7c-fiskr-poc-an2-lmd-entr-4-chunk
s7c-fiskr-poc-an2-lmd-entr-5-embed-idx
s7c-fiskr-poc-an2-lmd-entr-6-final
s7c-fiskr-poc-an2-lmd-ckpt-checkpoint
s7c-fiskr-poc-an2-lmd-oner-risk-clause
s7c-fiskr-poc-an2-lmd-addm-0-idx-copy
s7c-fiskr-poc-an2-lmd-addm-1-analyze
s7c-fiskr-poc-an2-lmd-addm-2-parse
s7c-fiskr-poc-an2-lmd-addm-3-toc
s7c-fiskr-poc-an2-lmd-addm-4-compare
s7c-fiskr-poc-an2-lmd-addm-5-embed
```

## Usage

```hcl
module "lambda" {
  source = "../../../modules/lambda"

  environment               = "poc"
  stack_name               = "itb-reviewer-indexing"
  vpc_id                   = module.vpc.vpc_id
  subnet_ids               = module.vpc.private_subnet_ids

  # S3 Configuration
  data_bucket_name         = "itb-reviewer-poc-ap-northeast-2"
  app_bucket_name          = "ai-app-d-an2-s3-air"

  # Secrets
  secret_name              = "aws-poc/opensearch/credentials"
  bedrock_secret_key       = "air-prd-an2-api-keys-bdr"

  # Bedrock Configuration
  bedrock_region           = "ap-northeast-2"
  bedrock_assume_id        = "913388732877"
  embedding_dimension      = 1024

  # IAM
  assume_role_arn_in_aiapp = "arn:aws:iam::543239041290:role/air-access-s3-from-itb-reviewer"

  # Document Processing
  synonyms                 = "F96158235"
  compound_nouns          = "F84133870"

  # Source paths
  lambda_source_path      = "../../../../../indexing/src"
  common_layer_path       = "../../../../../indexing/src/layers/common_layer"
}
```

## Prerequisites

- Lambda function source code must exist at the specified paths
- VPC and subnets must be created first
- S3 buckets and IAM roles referenced must exist

## Outputs

- `security_group_id`: Security group ID for Lambda functions
- `common_layer_arn`: ARN of the common Lambda layer
- `*_function_arn`: ARNs for each Lambda function
- `lambda_function_names`: Map of all Lambda function names
- `sagemaker_execution_role_arn`: ARN of SageMaker execution role
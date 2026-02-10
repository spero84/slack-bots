# Terraform Infrastructure as Code

## Project Overview

This project contains Terraform and Terragrunt configurations converted from:
1. **AWS CDK** (`luga-infra`) - Core AWS infrastructure
2. **AWS SAM** (`indexing`) - Serverless Lambda functions and Step Functions

## Naming Convention

### Format

```
{prefix}-{project_short}-{env}-{region_short}-{resource_short}
```

**Example**: `s7c-fiskr-poc-an2-vpc`

### Components

| Component | Description | Example |
|-----------|-------------|---------|
| prefix | Organization identifier | `s7c` |
| project_short | Project short name | `jeajung` |
| env | Environment | `poc`, `dev`, `staging`, `prod` |
| region_short | AWS region abbreviation | `an2` (ap-northeast-2), `ue1` (us-east-1) |
| resource_short | AWS service abbreviation | `vpc`, `s3`, `ec2`, etc. |

### Environment Variables (dev04)

```hcl
prefix        = "s7c"
project_short = "jeajung"
env           = "poc"
region_short  = "an2"  # ap-northeast-2
```

### Resource Short Abbreviations

| Category | Service | Abbreviation |
|----------|---------|--------------|
| **Compute** | EC2 | `ec2` |
| | Lambda | `lmd` |
| | ECS | `ecs` |
| | ECR | `ecr` |
| **Network** | VPC | `vpc` |
| | Subnet | `sn` |
| | Route Table | `rtb` |
| | Internet Gateway | `igw` |
| | NAT Gateway | `nat` |
| | Security Group | `sg` |
| | VPC Endpoint | `vpce` |
| | Application Load Balancer | `alb` |
| | Network Load Balancer | `nlb` |
| | Transit Gateway | `tgw` |
| **Storage** | S3 | `s3` |
| | EBS | `ebs` |
| | EFS | `efs` |
| **Database** | RDS | `rds` |
| | DynamoDB | `ddb` |
| | ElastiCache | `ec` |
| **Security** | IAM | `iam` |
| | KMS | `kms` |
| | Secrets Manager | `sm` |
| | Cognito | `cog` |
| **CI/CD** | CodeCommit | `cc` |
| | CodeBuild | `cb` |
| | CodeDeploy | `cd` |
| | CodePipeline | `cp` |
| **Monitoring** | CloudWatch | `cw` |
| | CloudWatch Logs | `cwl` |
| **AI/ML** | Bedrock | `bdr` |
| | SageMaker | `sgm` |
| **Messaging** | SQS | `sqs` |
| | SNS | `sns` |
| | Step Functions | `sfn` |
| **Others** | IAM Role | `role` |
| | IAM Policy | `pol` |

### Usage in Terraform

```hcl
# Base prefix (without resource_short)
name_prefix = "${local.prefix}-${local.project_short}-${local.env}-${local.region_short}"
# Result: "s7c-jeajung-poc-an2"

# Full resource name
vpc_name = "${local.name_prefix}-${local.resource_short.vpc}"
# Result: "s7c-jeajung-poc-an2-vpc"
```

### Common Tags

```hcl
tags = {
  Environment  = "poc"
  Project      = "jeajung"
  ManagedBy    = "Terraform"
  NamingPrefix = "s7c-jeajung-poc-an2"
}
```

## Directory Structure

```
terraform/
├── modules/                    # Reusable Terraform modules
│   ├── s3/                     # S3 buckets (Access Log, Data)
│   ├── vpc/                    # VPC, Subnets, Route Tables, VPC Endpoints, Security Groups
│   ├── iam/                    # IAM roles for ECS, CodeBuild, CodeDeploy, CodePipeline
│   ├── ecr/                    # ECR repositories (UI, API)
│   ├── ecs/                    # ECS Cluster, CloudWatch Log Groups
│   ├── dynamodb/               # DynamoDB tables (Conversation, Connection, Admin)
│   ├── cognito/                # Cognito User Pool, Client, Domain
│   ├── cicd/                   # CodeCommit, CodeBuild, CodePipeline
│   ├── lambda/                 # Lambda functions and layers
│   └── step-functions/         # Step Functions state machines
├── environments/               # Terragrunt environment configurations
│   ├── terragrunt.hcl          # Root configuration (Provider, Backend, Versions)
│   └── poc/                    # PoC environment
│       ├── env.hcl             # Environment variables
│       ├── s3/
│       ├── vpc/
│       ├── iam/
│       ├── ecr/
│       ├── ecs/
│       ├── dynamodb/
│       ├── cognito/
│       ├── cicd/
│       ├── lambda/
│       └── step-functions/
└── src/                        # Lambda source code
    ├── functions/              # Lambda function code
    │   ├── entr_2/             # Document listing
    │   ├── entr_3/             # Document parsing (Vision API)
    │   ├── entr_4/             # Chunking
    │   ├── entr_5/             # Embedding & indexing
    │   ├── checkpoints/        # Checkpoint processing
    │   ├── onerous_claude/     # Onerous clause analysis
    │   └── addendum/           # Addendum processing functions
    │       ├── addm_entr_1/    # File analysis
    │       ├── addm_entr_2/    # Document parsing
    │       ├── addm_entr_3/    # TOC tree chunking
    │       ├── addm_entr_4/    # OpenSearch comparison
    │       ├── addm_entr_5/    # Feature generation & indexing
    │       ├── addm_entr_6/    # Final processing
    │       ├── addm_index_copy/# Index copy
    │       └── statemachine/   # Step Functions ASL definitions
    └── layers/
        └── common_layer/       # Shared Python libraries
```

## Prerequisites

### Required Tools
- Terraform >= 1.0
- Terragrunt >= 0.45
- AWS CLI configured with appropriate credentials

### Backend Setup (First Time Only)

```bash
# Create S3 bucket for Terraform state
aws s3api create-bucket \
    --bucket terraform-state-poc-ap-northeast-2 \
    --region ap-northeast-2 \
    --create-bucket-configuration LocationConstraint=ap-northeast-2

# Create DynamoDB table for state locking
aws dynamodb create-table \
    --table-name terraform-locks-poc \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --provisioned-throughput ReadCapacityUnits=5,WriteCapacityUnits=5
```

## Deployment

### Deploy All Resources

```bash
cd environments/poc
terragrunt run --all apply
```

### Deploy Individual Modules

```bash
# Deploy in order of dependencies
cd environments/poc/vpc && terragrunt apply
cd environments/poc/iam && terragrunt apply
cd environments/poc/s3 && terragrunt apply
cd environments/poc/ecr && terragrunt apply
cd environments/poc/ecs && terragrunt apply
cd environments/poc/dynamodb && terragrunt apply
cd environments/poc/cognito && terragrunt apply
cd environments/poc/cicd && terragrunt apply
cd environments/poc/lambda && terragrunt apply
cd environments/poc/step-functions && terragrunt apply
```

## Module Dependencies

```
vpc
 └── lambda (uses vpc_id, subnet_ids)
     └── step-functions (uses lambda ARNs)

iam
 └── ecs (uses task roles)
 └── cicd (uses build/deploy/pipeline roles)
 └── lambda (uses execution roles)

s3
 └── lambda (uses bucket names)

ecr
 └── ecs (uses repository URIs)
 └── cicd (uses repository for build output)

dynamodb (standalone)
cognito (standalone)
```

## Environment Configuration

Edit `environments/poc/env.hcl` to customize:

```hcl
locals {
  environment   = "poc"
  project_name  = "your-project-name"
  aws_region    = "ap-northeast-2"
  vpc_cidr      = "10.0.0.0/24"

  # Lambda configuration
  data_bucket_name = "your-data-bucket"
  secret_name      = "your-secret-name"
  bedrock_region   = "ap-northeast-2"
}
```

## Lambda Functions

| Function | Purpose |
|----------|---------|
| entr_2 | List documents from S3 |
| entr_3 | Parse documents using Vision API |
| entr_4 | Chunk documents |
| entr_5 | Generate embeddings and index to OpenSearch |
| checkpoints | Handle long-running checkpoint processing |
| onerous_claude | Analyze onerous clauses using Claude |
| addm_entr_1-6 | Addendum document processing pipeline |

## Step Functions Workflows

| State Machine | Purpose |
|---------------|---------|
| document-preprocessing | Pre-process documents |
| document-loading | Load and parse documents |
| document-processing | Full indexing pipeline |
| parent-pipeline | Orchestrate preprocessing + processing |
| checkpoints | Checkpoint-based processing |
| checkpoints-batch | Batch checkpoint processing |
| onerous-clause-processing | Risk clause analysis |
| addendum-processing | Addendum workflow |

## Common Commands

```bash
# Plan changes
terragrunt run --all plan

# Apply changes
terragrunt run --all apply

# Destroy resources
terragrunt run --all destroy

# Format code
terraform fmt -recursive

# Validate configuration
terragrunt run --all validate
```

## Troubleshooting

### State Lock Issues
```bash
# Force unlock (use with caution)
terragrunt force-unlock <LOCK_ID>
```

### Module Dependency Issues
```bash
# Clear cache and retry
rm -rf .terragrunt-cache
terragrunt run --all apply
```

## Source Projects

- **luga-infra (CDK)**: `/Users/sunghyon/Projects/Searchdoc/infra-iac/aws-baseline/luga-infra`
- **indexing (SAM)**: `/Users/sunghyon/Projects/Searchdoc/infra-iac/indexing`

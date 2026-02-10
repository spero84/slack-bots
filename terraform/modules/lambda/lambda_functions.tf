# Lambda function configurations
locals {
  default_lambda_config = {
    runtime     = "python3.11"
    timeout     = 900
    handler     = "main.lambda_handler"
    memory_size = 3008  # Max for arm64 architecture
  }

  default_environment_variables = {
    APP_NAME                        = var.stack_name
    REGION_NAME                     = data.aws_region.current.name
    BUCKET_NAME                     = var.data_bucket_name
    PROJECT_ROOT                    = "/"
    BEDROCK_REGION                  = var.bedrock_region
    BEDROCK_SECRET_KEY              = var.bedrock_secret_key
    BEDROCK_ASSUME_ROLE_ACCOUNT_ID  = var.bedrock_assume_id
    APP_BUCKET_NAME                 = var.app_bucket_name
    APP_ASSUME_ROLE_ARN             = var.assume_role_arn_in_aiapp
    APP_ACCOUNT_ID                  = data.aws_caller_identity.current.account_id
  }
}

# Document Listing Function
resource "aws_lambda_function" "entr2" {
  function_name = "${var.stack_name}-entr-2-doc-list"
  role          = aws_iam_role.lambda_execution.arn

  filename         = data.archive_file.entr2.output_path
  source_code_hash = data.archive_file.entr2.output_base64sha256

  runtime     = local.default_lambda_config.runtime
  handler     = local.default_lambda_config.handler
  timeout     = local.default_lambda_config.timeout
  memory_size = local.default_lambda_config.memory_size

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = merge(
      local.default_environment_variables,
      {
        SECRET_NAME       = var.secret_name
        DIMENSIONS        = var.embedding_dimension
        SYNONYMS          = var.synonyms
        COMPOUND_NOUNS    = var.compound_nouns
        DEBUG             = "true"
        LAMBDA_TIMEOUT_BUFFER = "180"
        MAX_WORKERS       = "5"
        BATCH_SIZE        = "5"
        API_CALL_DELAY    = "2.0"
        MAX_RETRIES       = "10"
      }
    )
  }

  layers = [aws_lambda_layer_version.common.arn]

  tags = var.tags
}

data "archive_file" "entr2" {
  type        = "zip"
  source_dir  = "${var.lambda_source_path}/functions/entr_2_doc_listing"
  output_path = "${path.module}/tmp/entr2.zip"
}

# Document Parsing Function with Vision API
resource "aws_lambda_function" "entr3" {
  function_name = "${var.stack_name}-entr-3-doc-parse"
  role          = aws_iam_role.lambda_execution.arn

  filename         = data.archive_file.entr3.output_path
  source_code_hash = data.archive_file.entr3.output_base64sha256

  runtime     = local.default_lambda_config.runtime
  handler     = local.default_lambda_config.handler
  timeout     = local.default_lambda_config.timeout
  memory_size = local.default_lambda_config.memory_size

  ephemeral_storage {
    size = 5120
  }

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = merge(
      local.default_environment_variables,
      {
        SECRET_NAME       = var.secret_name
        DEBUG             = "true"
        LAMBDA_TIMEOUT_BUFFER = "180"
        MAX_WORKERS       = "5"
        BATCH_SIZE        = "5"
        API_CALL_DELAY    = "2.0"
        MAX_RETRIES       = "10"
      }
    )
  }

  layers = [aws_lambda_layer_version.common.arn]

  tags = var.tags
}

data "archive_file" "entr3" {
  type        = "zip"
  source_dir  = "${var.lambda_source_path}/functions/entr_3_doc_parsing"
  output_path = "${path.module}/tmp/entr3.zip"
}

# Document Chunking Function
resource "aws_lambda_function" "entr4" {
  function_name = "${var.stack_name}-entr-4-chunk"
  role          = aws_iam_role.lambda_execution.arn

  filename         = data.archive_file.entr4.output_path
  source_code_hash = data.archive_file.entr4.output_base64sha256

  runtime     = local.default_lambda_config.runtime
  handler     = local.default_lambda_config.handler
  timeout     = local.default_lambda_config.timeout
  memory_size = local.default_lambda_config.memory_size

  ephemeral_storage {
    size = 5120
  }

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = merge(
      local.default_environment_variables,
      {
        SECRET_NAME = var.secret_name
        DEBUG       = "true"
      }
    )
  }

  layers = [aws_lambda_layer_version.common.arn]

  tags = var.tags
}

data "archive_file" "entr4" {
  type        = "zip"
  source_dir  = "${var.lambda_source_path}/functions/entr_4_chunking"
  output_path = "${path.module}/tmp/entr4.zip"
}

# Embedding & Indexing Function
resource "aws_lambda_function" "entr5" {
  function_name = "${var.stack_name}-entr-5-embed-idx"
  role          = aws_iam_role.lambda_execution.arn

  filename         = data.archive_file.entr5.output_path
  source_code_hash = data.archive_file.entr5.output_base64sha256

  runtime     = local.default_lambda_config.runtime
  handler     = local.default_lambda_config.handler
  timeout     = local.default_lambda_config.timeout
  memory_size = 2048  # Different from default

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = merge(
      local.default_environment_variables,
      {
        SECRET_NAME    = var.secret_name
        DIMENSIONS     = var.embedding_dimension
        SYNONYMS       = var.synonyms
        COMPOUND_NOUNS = var.compound_nouns
        DEBUG          = "true"
      }
    )
  }

  layers = [aws_lambda_layer_version.common.arn]

  tags = var.tags
}

data "archive_file" "entr5" {
  type        = "zip"
  source_dir  = "${var.lambda_source_path}/functions/entr_5_indexing"
  output_path = "${path.module}/tmp/entr5.zip"
}

# Final Processing Function
resource "aws_lambda_function" "entr6" {
  function_name = "${var.stack_name}-entr-6-final"
  role          = aws_iam_role.lambda_execution.arn

  filename         = data.archive_file.entr6.output_path
  source_code_hash = data.archive_file.entr6.output_base64sha256

  runtime     = local.default_lambda_config.runtime
  handler     = local.default_lambda_config.handler
  timeout     = local.default_lambda_config.timeout
  memory_size = 3008

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = merge(
      local.default_environment_variables,
      {
        FUNCTION_NAME = "ADDM-ENTR-6"
        SECRET_NAME   = var.secret_name
      }
    )
  }

  layers = [aws_lambda_layer_version.common.arn]

  tags = var.tags
}

data "archive_file" "entr6" {
  type        = "zip"
  source_dir  = "${var.lambda_source_path}/functions/addendum/addm_entr_6"
  output_path = "${path.module}/tmp/entr6.zip"
}

# Checkpoint Processing Function
resource "aws_lambda_function" "checkpoints" {
  function_name = "${var.stack_name}-ckpt-checkpoint"
  role          = aws_iam_role.checkpoints_function.arn

  filename         = data.archive_file.checkpoints.output_path
  source_code_hash = data.archive_file.checkpoints.output_base64sha256

  runtime     = local.default_lambda_config.runtime
  handler     = local.default_lambda_config.handler
  timeout     = local.default_lambda_config.timeout
  memory_size = local.default_lambda_config.memory_size

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      BEDROCK_TITAN_REGION            = var.bedrock_region
      BEDROCK_CLAUDE_V3_REGION        = var.bedrock_region
      BEDROCK_CLAUDE_V4_REGION        = var.bedrock_region
      BUCKET_NAME                     = var.data_bucket_name
      SECRET_OPENSEARCH               = var.secret_name
      SECRET_NAME                     = var.secret_name
      REGION_NAME                     = data.aws_region.current.name
      DEBUG                           = "true"
      BEDROCK_ASSUME_ROLE_ACCOUNT_ID = var.bedrock_assume_id
    }
  }

  layers = [aws_lambda_layer_version.common.arn]

  tags = var.tags
}

data "archive_file" "checkpoints" {
  type        = "zip"
  source_dir  = "${var.lambda_source_path}/functions/checkpoints"
  output_path = "${path.module}/tmp/checkpoints.zip"
}

# Risk Clause Analysis Function
resource "aws_lambda_function" "onerous_claude" {
  function_name = "${var.stack_name}-oner-risk-clause"
  role          = aws_iam_role.onerous_claude_function.arn

  filename         = data.archive_file.onerous_claude.output_path
  source_code_hash = data.archive_file.onerous_claude.output_base64sha256

  runtime     = local.default_lambda_config.runtime
  handler     = local.default_lambda_config.handler
  timeout     = local.default_lambda_config.timeout
  memory_size = local.default_lambda_config.memory_size

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      BUCKET_NAME                     = var.data_bucket_name
      SECRET_OPENSEARCH               = var.secret_name
      SECRET_NAME                     = var.secret_name
      REGION_NAME                     = data.aws_region.current.name
      BEDROCK_CLAUDE_V3_REGION        = var.bedrock_region
      BEDROCK_CLAUDE_V4_REGION        = var.bedrock_region
      BEDROCK_TITAN_REGION            = var.bedrock_region
      DEBUG                           = "true"
      BEDROCK_ASSUME_ROLE_ACCOUNT_ID = var.bedrock_assume_id
      # Note: These would need to be retrieved from Secrets Manager or passed as variables
      # OS_ENDPOINT, OS_USERNAME, OS_PASSWORD are handled via Secrets Manager
    }
  }

  layers = [aws_lambda_layer_version.common.arn]

  tags = var.tags
}

data "archive_file" "onerous_claude" {
  type        = "zip"
  source_dir  = "${var.lambda_source_path}/functions/onerous_claude"
  output_path = "${path.module}/tmp/onerous_claude.zip"
}

# Default Lambda Execution Role
resource "aws_iam_role" "lambda_execution" {
  name = "${var.stack_name}-lambda-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = var.tags
}

# Attach AWS managed policy for VPC Lambda execution
resource "aws_iam_role_policy_attachment" "lambda_vpc_execution" {
  role       = aws_iam_role.lambda_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# Custom policy for Lambda execution
resource "aws_iam_role_policy" "lambda_execution" {
  name = "${var.stack_name}-lambda-execution-policy"
  role = aws_iam_role.lambda_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject"
        ]
        Resource = "arn:aws:s3:::${var.data_bucket_name}/*"
      },
      {
        Effect = "Allow"
        Action = "s3:ListBucket"
        Resource = "arn:aws:s3:::${var.data_bucket_name}"
      },
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = "arn:aws:secretsmanager:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:secret:*"
      },
      {
        Effect = "Allow"
        Action = "sts:AssumeRole"
        Resource = var.assume_role_arn_in_aiapp
      },
      {
        Effect = "Allow"
        Action = "sts:AssumeRole"
        Resource = "arn:aws:iam::${var.bedrock_assume_id}:role/ai-workloads-ou-role-bedrock-app"
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/anthropic.claude-3-haiku-20240307-v1:0",
          "arn:aws:bedrock:*::foundation-model/anthropic.claude-3-7-sonnet-20250219-v1:0",
          "arn:aws:bedrock:*::foundation-model/anthropic.claude-3-5-haiku-20241022-v1:0",
          "arn:aws:bedrock:*::foundation-model/amazon.titan-embed-text-v2:0",
          "arn:aws:bedrock:*:*:*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:DeleteParameter",
          "ssm:PutParameter"
        ]
        Resource = "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/dp/*"
      },
      {
        Effect = "Allow"
        Action = [
          "ec2:CreateNetworkInterface",
          "ec2:DescribeNetworkInterfaces",
          "ec2:DeleteNetworkInterface",
          "ec2:AssignPrivateIpAddresses",
          "ec2:UnassignPrivateIpAddresses"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData"
        ]
        Resource = "*"
      }
    ]
  })
}
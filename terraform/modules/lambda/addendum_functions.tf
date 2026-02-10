# Addendum Processing Lambda Functions

# Addendum File Analysis Function
resource "aws_lambda_function" "addm_entr1" {
  function_name = "${var.stack_name}-addm-1-analyze"
  role          = aws_iam_role.addendum_lambda.arn

  filename         = data.archive_file.addm_entr1.output_path
  source_code_hash = data.archive_file.addm_entr1.output_base64sha256

  runtime     = "python3.11"
  handler     = "main.lambda_handler"
  timeout     = 900
  memory_size = 3008

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      FUNCTION_NAME                   = "ADDM-ENTR-1"
      SECRET_NAME                     = var.secret_name
      BUCKET_NAME                     = var.data_bucket_name
      REGION_NAME                     = data.aws_region.current.name
      APP_ASSUME_ROLE_ARN             = var.assume_role_arn_in_aiapp
      APP_BUCKET_NAME                 = var.app_bucket_name
      BEDROCK_ASSUME_ROLE_ACCOUNT_ID  = var.bedrock_assume_id
      BEDROCK_REGION                  = var.bedrock_region
    }
  }

  layers = [aws_lambda_layer_version.common.arn]

  tags = var.tags
}

data "archive_file" "addm_entr1" {
  type        = "zip"
  source_dir  = "${var.lambda_source_path}/functions/addendum/addm_entr_1"
  output_path = "${path.module}/tmp/addm_entr1.zip"
}

# Addendum Index Copy Function (OpenSearch)
resource "aws_lambda_function" "addm_index_copy" {
  function_name = "${var.stack_name}-addm-0-idx-copy"
  role          = aws_iam_role.addendum_lambda.arn

  filename         = data.archive_file.addm_index_copy.output_path
  source_code_hash = data.archive_file.addm_index_copy.output_base64sha256

  runtime     = "python3.11"
  handler     = "main.lambda_handler"
  timeout     = 900
  memory_size = 3008

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      FUNCTION_NAME                   = "ADDM-INDEX-COPY"
      SECRET_NAME                     = var.secret_name
      REGION_NAME                     = data.aws_region.current.name
      DIMENSIONS                      = var.embedding_dimension
      BEDROCK_ASSUME_ROLE_ACCOUNT_ID = var.bedrock_assume_id
    }
  }

  layers = [aws_lambda_layer_version.common.arn]

  tags = var.tags
}

data "archive_file" "addm_index_copy" {
  type        = "zip"
  source_dir  = "${var.lambda_source_path}/functions/addendum/addm_index_copy"
  output_path = "${path.module}/tmp/addm_index_copy.zip"
}

# Addendum Document Parsing Function
resource "aws_lambda_function" "addm_entr2" {
  function_name = "${var.stack_name}-addm-2-parse"
  role          = aws_iam_role.addendum_lambda.arn

  filename         = data.archive_file.addm_entr2.output_path
  source_code_hash = data.archive_file.addm_entr2.output_base64sha256

  runtime     = "python3.11"
  handler     = "main.lambda_handler"
  timeout     = 900
  memory_size = 3008

  ephemeral_storage {
    size = 1024
  }

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      FUNCTION_NAME                   = "ADDM-ENTR-2"
      SECRET_NAME                     = var.secret_name
      BUCKET_NAME                     = var.data_bucket_name
      REGION_NAME                     = data.aws_region.current.name
      APP_ASSUME_ROLE_ARN             = var.assume_role_arn_in_aiapp
      BEDROCK_ASSUME_ROLE_ACCOUNT_ID  = var.bedrock_assume_id
      APP_BUCKET_NAME                 = var.app_bucket_name
      BEDROCK_REGION                  = var.bedrock_region
      LAMBDA_TIMEOUT_BUFFER           = "180"
      MAX_WORKERS                     = "5"
      BATCH_SIZE                      = "5"
      API_CALL_DELAY                  = "2.0"
      MAX_RETRIES                     = "10"
    }
  }

  layers = [aws_lambda_layer_version.common.arn]

  tags = var.tags
}

data "archive_file" "addm_entr2" {
  type        = "zip"
  source_dir  = "${var.lambda_source_path}/functions/addendum/addm_entr_2"
  output_path = "${path.module}/tmp/addm_entr2.zip"
}

# Addendum TOC Tree Chunking Function
resource "aws_lambda_function" "addm_entr3" {
  function_name = "${var.stack_name}-addm-3-toc"
  role          = aws_iam_role.addendum_lambda.arn

  filename         = data.archive_file.addm_entr3.output_path
  source_code_hash = data.archive_file.addm_entr3.output_base64sha256

  runtime     = "python3.11"
  handler     = "main.lambda_handler"
  timeout     = 900
  memory_size = 3008

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      FUNCTION_NAME                   = "ADDM-ENTR-3"
      SECRET_NAME                     = var.secret_name
      BUCKET_NAME                     = var.data_bucket_name
      REGION_NAME                     = data.aws_region.current.name
      APP_ASSUME_ROLE_ARN             = var.assume_role_arn_in_aiapp
      BEDROCK_ASSUME_ROLE_ACCOUNT_ID  = var.bedrock_assume_id
      APP_BUCKET_NAME                 = var.app_bucket_name
      BEDROCK_REGION                  = var.bedrock_region
    }
  }

  layers = [aws_lambda_layer_version.common.arn]

  tags = var.tags
}

data "archive_file" "addm_entr3" {
  type        = "zip"
  source_dir  = "${var.lambda_source_path}/functions/addendum/addm_entr_3"
  output_path = "${path.module}/tmp/addm_entr3.zip"
}

# Addendum OpenSearch Comparison Function
resource "aws_lambda_function" "addm_entr4" {
  function_name = "${var.stack_name}-addm-4-compare"
  role          = aws_iam_role.addendum_lambda.arn

  filename         = data.archive_file.addm_entr4.output_path
  source_code_hash = data.archive_file.addm_entr4.output_base64sha256

  runtime     = "python3.11"
  handler     = "main.lambda_handler"
  timeout     = 900
  memory_size = 3008

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      FUNCTION_NAME                   = "ADDM-ENTR-4"
      SECRET_NAME                     = var.secret_name
      BUCKET_NAME                     = var.data_bucket_name
      REGION_NAME                     = data.aws_region.current.name
      APP_ASSUME_ROLE_ARN             = var.assume_role_arn_in_aiapp
      BEDROCK_ASSUME_ROLE_ACCOUNT_ID  = var.bedrock_assume_id
      APP_BUCKET_NAME                 = var.app_bucket_name
      BEDROCK_REGION                  = var.bedrock_region
    }
  }

  layers = [aws_lambda_layer_version.common.arn]

  tags = var.tags
}

data "archive_file" "addm_entr4" {
  type        = "zip"
  source_dir  = "${var.lambda_source_path}/functions/addendum/addm_entr_4"
  output_path = "${path.module}/tmp/addm_entr4.zip"
}

# Addendum Embedding & Indexing Function
resource "aws_lambda_function" "addm_entr5" {
  function_name = "${var.stack_name}-addm-5-embed"
  role          = aws_iam_role.addendum_lambda.arn

  filename         = data.archive_file.addm_entr5.output_path
  source_code_hash = data.archive_file.addm_entr5.output_base64sha256

  runtime     = "python3.11"
  handler     = "main.lambda_handler"
  timeout     = 900
  memory_size = 3008

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      FUNCTION_NAME                   = "ADDM-ENTR-5"
      SECRET_NAME                     = var.secret_name
      BUCKET_NAME                     = var.data_bucket_name
      REGION_NAME                     = data.aws_region.current.name
      APP_ASSUME_ROLE_ARN             = var.assume_role_arn_in_aiapp
      BEDROCK_ASSUME_ROLE_ACCOUNT_ID  = var.bedrock_assume_id
      APP_BUCKET_NAME                 = var.app_bucket_name
      BEDROCK_REGION                  = var.bedrock_region
      DIMENSIONS                      = var.embedding_dimension
      SYNONYMS                        = var.synonyms
      COMPOUND_NOUNS                  = var.compound_nouns
    }
  }

  layers = [aws_lambda_layer_version.common.arn]

  tags = var.tags
}

data "archive_file" "addm_entr5" {
  type        = "zip"
  source_dir  = "${var.lambda_source_path}/functions/addendum/addm_entr_5"
  output_path = "${path.module}/tmp/addm_entr5.zip"
}

# IAM Role for Addendum Lambda Functions
resource "aws_iam_role" "addendum_lambda" {
  name = "${var.stack_name}-addendum-lambda-role"

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
resource "aws_iam_role_policy_attachment" "addendum_lambda_vpc_execution" {
  role       = aws_iam_role.addendum_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# Custom policy for Addendum Lambda Functions
resource "aws_iam_role_policy" "addendum_lambda" {
  name = "${var.stack_name}-addendum-lambda-policy"
  role = aws_iam_role.addendum_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation"
        ]
        Resource = "arn:aws:s3:::${var.data_bucket_name}"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:GetObjectVersion"
        ]
        Resource = "arn:aws:s3:::${var.data_bucket_name}/*"
      },
      {
        Effect = "Allow"
        Action = "sts:AssumeRole"
        Resource = var.assume_role_arn_in_aiapp
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/amazon.titan-embed-text-v2:0",
          "arn:aws:bedrock:*::foundation-model/anthropic.claude-3-haiku-20240307-v1:0",
          "arn:aws:bedrock:*::foundation-model/anthropic.claude-3-7-sonnet-20250219-v1:0",
          "arn:aws:bedrock:*::foundation-model/anthropic.claude-3-5-haiku-20241022-v1:0",
          "arn:aws:bedrock:*:*:*"
        ]
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
        Resource = "arn:aws:iam::${var.bedrock_assume_id}:role/ai-workloads-ou-role-bedrock-app"
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
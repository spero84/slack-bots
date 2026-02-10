#!/bin/bash

# Terraform Backend Setup Script
# This script creates S3 bucket and DynamoDB table for Terraform state management

set -e

# Configuration
ENVIRONMENT="${1:-dev04}"
REGION="${2:-ap-northeast-2}"
BUCKET_NAME="terraform-state-${ENVIRONMENT}-${REGION}"
TABLE_NAME="terraform-locks-${ENVIRONMENT}"

echo "============================================"
echo "Terraform Backend Setup"
echo "============================================"
echo "Environment: ${ENVIRONMENT}"
echo "Region: ${REGION}"
echo "S3 Bucket: ${BUCKET_NAME}"
echo "DynamoDB Table: ${TABLE_NAME}"
echo "============================================"

# Check AWS credentials
echo ""
echo "[1/4] Checking AWS credentials..."
aws sts get-caller-identity

# Create S3 bucket
echo ""
echo "[2/4] Creating S3 bucket for Terraform state..."
if aws s3api head-bucket --bucket "${BUCKET_NAME}" 2>/dev/null; then
    echo "Bucket ${BUCKET_NAME} already exists. Skipping..."
else
    aws s3api create-bucket \
        --bucket "${BUCKET_NAME}" \
        --region "${REGION}" \
        --create-bucket-configuration LocationConstraint="${REGION}"
    echo "Bucket ${BUCKET_NAME} created successfully."
fi

# Enable versioning on S3 bucket
echo ""
echo "[3/4] Enabling versioning on S3 bucket..."
aws s3api put-bucket-versioning \
    --bucket "${BUCKET_NAME}" \
    --versioning-configuration Status=Enabled
echo "Versioning enabled."

# Create DynamoDB table
echo ""
echo "[4/4] Creating DynamoDB table for state locking..."
if aws dynamodb describe-table --table-name "${TABLE_NAME}" --region "${REGION}" 2>/dev/null; then
    echo "Table ${TABLE_NAME} already exists. Skipping..."
else
    aws dynamodb create-table \
        --table-name "${TABLE_NAME}" \
        --attribute-definitions AttributeName=LockID,AttributeType=S \
        --key-schema AttributeName=LockID,KeyType=HASH \
        --billing-mode PAY_PER_REQUEST \
        --region "${REGION}"
    echo "Table ${TABLE_NAME} created successfully."

    # Wait for table to be active
    echo "Waiting for table to become active..."
    aws dynamodb wait table-exists --table-name "${TABLE_NAME}" --region "${REGION}"
    echo "Table is now active."
fi

echo ""
echo "============================================"
echo "Backend setup completed successfully!"
echo "============================================"
echo ""
echo "You can now run terragrunt commands:"
echo "  cd environments/${ENVIRONMENT}"
echo "  terragrunt run-all init"
echo "  terragrunt run-all plan"
echo "  terragrunt run-all apply"

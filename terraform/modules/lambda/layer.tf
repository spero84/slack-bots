# Common Layer for Lambda Functions
data "archive_file" "common_layer" {
  type        = "zip"
  source_dir  = var.common_layer_path
  output_path = "${path.module}/tmp/common_layer.zip"
}

resource "aws_lambda_layer_version" "common" {
  layer_name               = "${var.stack_name}-common-layer"
  filename                 = data.archive_file.common_layer.output_path
  source_code_hash         = data.archive_file.common_layer.output_base64sha256
  compatible_runtimes      = ["python3.11"]
  compatible_architectures = ["x86_64"]
  description              = "Common layer for Indexing Pipeline"

  lifecycle {
    create_before_destroy = true
  }
}
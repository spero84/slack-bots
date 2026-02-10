# Security Group for Lambda Functions
resource "aws_security_group" "lambda" {
  name_prefix = "${var.stack_name}-lambda-"
  description = "Security group for Lambda functions"
  vpc_id      = var.vpc_id

  tags = merge(
    var.tags,
    {
      Name = "${var.stack_name}-lambda-sg"
    }
  )
}

# Egress rule for HTTPS traffic only
resource "aws_security_group_rule" "lambda_egress_https" {
  type              = "egress"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.lambda.id
  description       = "Allow HTTPS outbound traffic"
}
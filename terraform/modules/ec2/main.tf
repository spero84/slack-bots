# Get latest Ubuntu 24.04 LTS AMI
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# Generate TLS private key
resource "tls_private_key" "slack_bot" {
  count     = var.create_key_pair ? 1 : 0
  algorithm = "RSA"
  rsa_bits  = 4096
}

# Create AWS key pair
resource "aws_key_pair" "slack_bot" {
  count      = var.create_key_pair ? 1 : 0
  key_name   = var.key_name
  public_key = tls_private_key.slack_bot[0].public_key_openssh

  tags = merge(var.tags, {
    Name = var.key_name
  })
}

# Save private key to local file
resource "local_file" "private_key" {
  count           = var.create_key_pair ? 1 : 0
  content         = tls_private_key.slack_bot[0].private_key_pem
  filename        = "${path.root}/${var.key_name}.pem"
  file_permission = "0400"
}

# Security Group for EC2
resource "aws_security_group" "slack_bot" {
  name        = "${var.project_name}-sg-slack-bot"
  description = "Security group for Slack Bot EC2 instance"
  vpc_id      = var.vpc_id

  # SSH access
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.allowed_ssh_cidr_blocks
    description = "SSH access"
  }

  # All outbound traffic
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound traffic"
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-sg-slack-bot"
  })
}

# IAM Role for EC2
resource "aws_iam_role" "slack_bot" {
  name = "${var.project_name}-role-slack-bot"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = merge(var.tags, {
    Name = "${var.project_name}-role-slack-bot"
  })
}

# IAM Instance Profile
resource "aws_iam_instance_profile" "slack_bot" {
  name = "${var.project_name}-profile-slack-bot"
  role = aws_iam_role.slack_bot.name
}

# Attach SSM policy for Session Manager (optional)
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.slack_bot.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Attach Secrets Manager read policy
resource "aws_iam_role_policy_attachment" "secrets" {
  role       = aws_iam_role.slack_bot.name
  policy_arn = "arn:aws:iam::aws:policy/SecretsManagerReadWrite"
}

# Attach Bedrock access policy
resource "aws_iam_role_policy_attachment" "bedrock" {
  role       = aws_iam_role.slack_bot.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonBedrockFullAccess"
}

# Attach S3 full access policy
resource "aws_iam_role_policy_attachment" "s3" {
  role       = aws_iam_role.slack_bot.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonS3FullAccess"
}

# Attach PowerUserAccess policy
resource "aws_iam_role_policy_attachment" "power_user" {
  role       = aws_iam_role.slack_bot.name
  policy_arn = "arn:aws:iam::aws:policy/PowerUserAccess"
}

# EC2 Instance
resource "aws_instance" "slack_bot" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [aws_security_group.slack_bot.id]
  key_name                    = var.create_key_pair ? aws_key_pair.slack_bot[0].key_name : var.key_name
  iam_instance_profile        = aws_iam_instance_profile.slack_bot.name
  associate_public_ip_address = true

  root_block_device {
    volume_size           = var.root_volume_size
    volume_type           = "gp3"
    delete_on_termination = true
    encrypted             = true
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-ec2-slack-bot"
  })
}

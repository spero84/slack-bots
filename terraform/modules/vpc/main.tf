# VPC
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = "${var.project_name}-vpc"
  }
}

# Internet Gateway
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${var.project_name}-igw"
  }
}

# =============================================
# Subnets
# =============================================

# Public Subnets (ALB, NAT Gateway)
resource "aws_subnet" "public" {
  count = length(var.availability_zones)

  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name = "${var.project_name}-sn-pub-${count.index == 0 ? "a" : "c"}"
    Type = "Public"
  }
}

# Network Subnets (VPC Endpoints)
resource "aws_subnet" "network" {
  count = length(var.availability_zones)

  vpc_id            = aws_vpc.main.id
  cidr_block        = var.network_subnet_cidrs[count.index]
  availability_zone = var.availability_zones[count.index]

  tags = {
    Name = "${var.project_name}-sn-pri-net-${count.index == 0 ? "a" : "c"}"
    Type = "Network"
  }
}

# App Subnets (ECS Cluster)
resource "aws_subnet" "app" {
  count = length(var.availability_zones)

  vpc_id            = aws_vpc.main.id
  cidr_block        = var.app_subnet_cidrs[count.index]
  availability_zone = var.availability_zones[count.index]

  tags = {
    Name = "${var.project_name}-sn-pri-app-${count.index == 0 ? "a" : "c"}"
    Type = "App"
  }
}

# Data Subnets (OpenSearch)
resource "aws_subnet" "data" {
  count = length(var.availability_zones)

  vpc_id            = aws_vpc.main.id
  cidr_block        = var.data_subnet_cidrs[count.index]
  availability_zone = var.availability_zones[count.index]

  tags = {
    Name = "${var.project_name}-sn-pri-data-${count.index == 0 ? "a" : "c"}"
    Type = "Data"
  }
}

# Lambda Subnets
resource "aws_subnet" "lambda" {
  count = length(var.availability_zones)

  vpc_id            = aws_vpc.main.id
  cidr_block        = var.lambda_subnet_cidrs[count.index]
  availability_zone = var.availability_zones[count.index]

  tags = {
    Name = "${var.project_name}-sn-pri-lmd-${count.index == 0 ? "a" : "c"}"
    Type = "Lambda"
  }
}

# =============================================
# NAT Gateway
# =============================================

# Elastic IPs for NAT Gateway
resource "aws_eip" "nat" {
  count  = length(var.availability_zones)
  domain = "vpc"

  tags = {
    Name = "${var.project_name}-eip-nat-${count.index == 0 ? "a" : "c"}"
  }

  depends_on = [aws_internet_gateway.main]
}

# NAT Gateway in Public Subnets
resource "aws_nat_gateway" "main" {
  count = length(var.availability_zones)

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = {
    Name = "${var.project_name}-nat-${count.index == 0 ? "a" : "c"}"
  }

  depends_on = [aws_internet_gateway.main]
}

# =============================================
# Route Tables
# =============================================

# Public Route Table
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "${var.project_name}-rtb-pub"
  }
}

# Network Route Table (no internet route - internal only)
resource "aws_route_table" "network" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${var.project_name}-rtb-net"
  }
}

# App Route Tables (per AZ for NAT HA)
resource "aws_route_table" "app" {
  count  = length(var.availability_zones)
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${var.project_name}-rtb-app-${count.index == 0 ? "a" : "c"}"
  }
}

resource "aws_route" "app_nat" {
  count = length(var.availability_zones)

  route_table_id         = aws_route_table.app[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.main[count.index].id
}

# Data Route Tables (per AZ for NAT HA)
resource "aws_route_table" "data" {
  count  = length(var.availability_zones)
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${var.project_name}-rtb-data-${count.index == 0 ? "a" : "c"}"
  }
}

resource "aws_route" "data_nat" {
  count = length(var.availability_zones)

  route_table_id         = aws_route_table.data[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.main[count.index].id
}

# Lambda Route Tables (per AZ for NAT HA)
resource "aws_route_table" "lambda" {
  count  = length(var.availability_zones)
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${var.project_name}-rtb-lmd-${count.index == 0 ? "a" : "c"}"
  }
}

resource "aws_route" "lambda_nat" {
  count = length(var.availability_zones)

  route_table_id         = aws_route_table.lambda[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.main[count.index].id
}

# =============================================
# Route Table Associations
# =============================================

# Public Subnet Associations
resource "aws_route_table_association" "public" {
  count = length(aws_subnet.public)

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Network Subnet Associations
resource "aws_route_table_association" "network" {
  count = length(aws_subnet.network)

  subnet_id      = aws_subnet.network[count.index].id
  route_table_id = aws_route_table.network.id
}

# App Subnet Associations (per AZ)
resource "aws_route_table_association" "app" {
  count = length(aws_subnet.app)

  subnet_id      = aws_subnet.app[count.index].id
  route_table_id = aws_route_table.app[count.index].id
}

# Data Subnet Associations (per AZ)
resource "aws_route_table_association" "data" {
  count = length(aws_subnet.data)

  subnet_id      = aws_subnet.data[count.index].id
  route_table_id = aws_route_table.data[count.index].id
}

# Lambda Subnet Associations (per AZ)
resource "aws_route_table_association" "lambda" {
  count = length(aws_subnet.lambda)

  subnet_id      = aws_subnet.lambda[count.index].id
  route_table_id = aws_route_table.lambda[count.index].id
}

# =============================================
# Security Groups
# =============================================

resource "aws_security_group" "endpoint" {
  name        = "${var.project_name}-sg-endpoint"
  description = "Security group for VPC endpoints"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-sg-endpoint"
  }
}

resource "aws_security_group" "ecs_task" {
  name        = "${var.project_name}-sg-ecs"
  description = "Security group for ECS tasks"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-sg-ecs"
  }
}

resource "aws_security_group" "opensearch" {
  name        = "${var.project_name}-sg-es"
  description = "Security group for OpenSearch domain"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-sg-es"
  }
}

resource "aws_security_group" "lambda" {
  name        = "${var.project_name}-sg-lmd"
  description = "Security group for Lambda functions"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow HTTPS outbound"
  }

  tags = {
    Name = "${var.project_name}-sg-lmd"
  }
}

# =============================================
# Security Group Rules
# =============================================

resource "aws_security_group_rule" "opensearch_from_vpc" {
  type              = "ingress"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  cidr_blocks       = [var.vpc_cidr]
  security_group_id = aws_security_group.opensearch.id
  description       = "Allow HTTPS traffic from VPC"
}

resource "aws_security_group_rule" "endpoint_from_ecs" {
  type                     = "ingress"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.ecs_task.id
  security_group_id        = aws_security_group.endpoint.id
  description              = "Allow HTTPS traffic from ECS tasks"
}

resource "aws_security_group_rule" "endpoint_from_lambda" {
  type                     = "ingress"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.lambda.id
  security_group_id        = aws_security_group.endpoint.id
  description              = "Allow HTTPS traffic from Lambda functions"
}

resource "aws_security_group_rule" "opensearch_from_lambda" {
  type                     = "ingress"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.lambda.id
  security_group_id        = aws_security_group.opensearch.id
  description              = "Allow HTTPS traffic from Lambda functions"
}

# =============================================
# VPC Endpoints - Interface (Network Subnets)
# Only created if enable_interface_endpoints is true
# =============================================

resource "aws_vpc_endpoint" "ecr_docker" {
  count = var.enable_interface_endpoints ? 1 : 0

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.region}.ecr.dkr"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.network[*].id
  security_group_ids  = [aws_security_group.endpoint.id]
  private_dns_enabled = true

  tags = {
    Name = "${var.project_name}-vpce-EcrDockerEndpoint"
  }
}

resource "aws_vpc_endpoint" "ecr_api" {
  count = var.enable_interface_endpoints ? 1 : 0

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.region}.ecr.api"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.network[*].id
  security_group_ids  = [aws_security_group.endpoint.id]
  private_dns_enabled = true

  tags = {
    Name = "${var.project_name}-vpce-EcrApiEndpoint"
  }
}

resource "aws_vpc_endpoint" "cloudwatch_logs" {
  count = var.enable_interface_endpoints ? 1 : 0

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.region}.logs"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.network[*].id
  security_group_ids  = [aws_security_group.endpoint.id]
  private_dns_enabled = true

  tags = {
    Name = "${var.project_name}-vpce-CloudWatchLogsEndpoint"
  }
}

resource "aws_vpc_endpoint" "cloudwatch_monitoring" {
  count = var.enable_interface_endpoints ? 1 : 0

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.region}.monitoring"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.network[*].id
  security_group_ids  = [aws_security_group.endpoint.id]
  private_dns_enabled = true

  tags = {
    Name = "${var.project_name}-vpce-CloudWatchEndpoint"
  }
}

resource "aws_vpc_endpoint" "secrets_manager" {
  count = var.enable_interface_endpoints ? 1 : 0

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.region}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.network[*].id
  security_group_ids  = [aws_security_group.endpoint.id]
  private_dns_enabled = true

  tags = {
    Name = "${var.project_name}-vpce-SecretsManagerEndpoint"
  }
}

resource "aws_vpc_endpoint" "kms" {
  count = var.enable_interface_endpoints ? 1 : 0

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.region}.kms"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.network[*].id
  security_group_ids  = [aws_security_group.endpoint.id]
  private_dns_enabled = true

  tags = {
    Name = "${var.project_name}-vpce-KmsEndpoint"
  }
}

resource "aws_vpc_endpoint" "sts" {
  count = var.enable_interface_endpoints ? 1 : 0

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.region}.sts"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.network[*].id
  security_group_ids  = [aws_security_group.endpoint.id]
  private_dns_enabled = true

  tags = {
    Name = "${var.project_name}-vpce-StsEndpoint"
  }
}

resource "aws_vpc_endpoint" "ecs" {
  count = var.enable_interface_endpoints ? 1 : 0

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.region}.ecs"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.network[*].id
  security_group_ids  = [aws_security_group.endpoint.id]
  private_dns_enabled = true

  tags = {
    Name = "${var.project_name}-vpce-EcsEndpoint"
  }
}

resource "aws_vpc_endpoint" "ecs_agent" {
  count = var.enable_interface_endpoints ? 1 : 0

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.region}.ecs-agent"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.network[*].id
  security_group_ids  = [aws_security_group.endpoint.id]
  private_dns_enabled = true

  tags = {
    Name = "${var.project_name}-vpce-EcsAgentEndpoint"
  }
}

resource "aws_vpc_endpoint" "ecs_telemetry" {
  count = var.enable_interface_endpoints ? 1 : 0

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.region}.ecs-telemetry"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.network[*].id
  security_group_ids  = [aws_security_group.endpoint.id]
  private_dns_enabled = true

  tags = {
    Name = "${var.project_name}-vpce-EcsTelemetryEndpoint"
  }
}

resource "aws_vpc_endpoint" "bedrock" {
  count = var.enable_interface_endpoints ? 1 : 0

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.region}.bedrock"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.network[*].id
  security_group_ids  = [aws_security_group.endpoint.id]
  private_dns_enabled = true

  tags = {
    Name = "${var.project_name}-vpce-BedrockEndpoint"
  }
}

resource "aws_vpc_endpoint" "bedrock_agent" {
  count = var.enable_interface_endpoints ? 1 : 0

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.region}.bedrock-agent"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.network[*].id
  security_group_ids  = [aws_security_group.endpoint.id]
  private_dns_enabled = true

  tags = {
    Name = "${var.project_name}-vpce-BedrockAgentEndpoint"
  }
}

resource "aws_vpc_endpoint" "bedrock_runtime" {
  count = var.enable_interface_endpoints ? 1 : 0

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.region}.bedrock-runtime"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.network[*].id
  security_group_ids  = [aws_security_group.endpoint.id]
  private_dns_enabled = true

  tags = {
    Name = "${var.project_name}-vpce-BedrockRuntimeEndpoint"
  }
}

resource "aws_vpc_endpoint" "ssm" {
  count = var.enable_interface_endpoints ? 1 : 0

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.region}.ssm"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.network[*].id
  security_group_ids  = [aws_security_group.endpoint.id]
  private_dns_enabled = true

  tags = {
    Name = "${var.project_name}-vpce-ssm"
  }
}

# =============================================
# VPC Endpoints - Gateway
# =============================================

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = concat(
    [aws_route_table.network.id],
    aws_route_table.app[*].id,
    aws_route_table.data[*].id,
    aws_route_table.lambda[*].id
  )

  tags = {
    Name = "${var.project_name}-vpce-S3Endpoint"
  }
}

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = concat(
    [aws_route_table.network.id],
    aws_route_table.app[*].id,
    aws_route_table.data[*].id,
    aws_route_table.lambda[*].id
  )

  tags = {
    Name = "${var.project_name}-vpce-DynamoDBEndpoint"
  }
}

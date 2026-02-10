# State migration: Move existing resources to new for_each keys
moved {
  from = aws_security_group.alb
  to   = aws_security_group.alb["ess"]
}

moved {
  from = aws_security_group_rule.ecs_from_alb
  to   = aws_security_group_rule.ecs_from_alb["ess"]
}

moved {
  from = aws_lb.main
  to   = aws_lb.main["ess"]
}

moved {
  from = aws_lb_target_group.api
  to   = aws_lb_target_group.api["ess"]
}

moved {
  from = aws_lb_target_group.ui
  to   = aws_lb_target_group.ui["ess"]
}

moved {
  from = aws_lb_listener.https
  to   = aws_lb_listener.https["ess"]
}

moved {
  from = aws_lb_listener.http
  to   = aws_lb_listener.http["ess"]
}

moved {
  from = aws_lb_listener_rule.api
  to   = aws_lb_listener_rule.api["ess"]
}

moved {
  from = aws_lb_listener_rule.websocket
  to   = aws_lb_listener_rule.websocket["ess"]
}

moved {
  from = aws_route53_record.alb
  to   = aws_route53_record.alb["ess"]
}

locals {
  lb_map = { for lb in var.load_balancers : lb.name => lb }

  # Description names to preserve existing casing (avoid security group replacement)
  sg_description_names = {
    "ess"     = "ess"      # existing: "Security group for ess Application Load Balancer"
    "console" = "Console"  # existing: "Security group for Console Application Load Balancer"
  }
}

# ALB Security Groups
resource "aws_security_group" "alb" {
  for_each = local.lb_map

  name        = "${var.project_name}-sg-${each.key}-alb"
  description = "Security group for ${lookup(local.sg_description_names, each.key, each.key)} Application Load Balancer"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = each.value.allowed_cidr_blocks
    description = "Allow HTTPS traffic"
  }

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = each.value.allowed_cidr_blocks
    description = "Allow HTTP traffic"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound traffic"
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-sg-${each.key}-alb"
  })
}

# Security Group Rules: Allow traffic from ALB to ECS tasks
resource "aws_security_group_rule" "ecs_from_alb" {
  for_each = local.lb_map

  type                     = "ingress"
  from_port                = 0
  to_port                  = 65535
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.alb[each.key].id
  security_group_id        = var.ecs_security_group_id
  description              = "Allow traffic from ${each.key} ALB to ECS tasks"
}

# Application Load Balancers
resource "aws_lb" "main" {
  for_each = local.lb_map

  name               = "${var.project_name}-${each.key}-alb"
  internal           = each.value.internal
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb[each.key].id]
  subnets            = var.subnet_ids

  enable_deletion_protection = var.enable_deletion_protection

  tags = merge(var.tags, {
    Name = "${var.project_name}-${each.key}-alb"
  })
}

# Target Groups for API Services
resource "aws_lb_target_group" "api" {
  for_each = local.lb_map

  name        = "${var.project_name}-tg-${each.key}-api"
  port        = each.value.api_container_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    enabled             = true
    healthy_threshold   = 2
    interval            = 30
    matcher             = "200-299"
    path                = each.value.api_health_check
    port                = "traffic-port"
    protocol            = "HTTP"
    timeout             = 5
    unhealthy_threshold = 3
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-tg-${each.key}-api"
  })
}

# Target Groups for UI Services
resource "aws_lb_target_group" "ui" {
  for_each = local.lb_map

  name        = "${var.project_name}-tg-${each.key}-ui"
  port        = each.value.ui_container_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    enabled             = true
    healthy_threshold   = 2
    interval            = 30
    matcher             = "200-299"
    path                = each.value.ui_health_check
    port                = "traffic-port"
    protocol            = "HTTP"
    timeout             = 5
    unhealthy_threshold = 3
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-tg-${each.key}-ui"
  })
}

# HTTPS Listeners
resource "aws_lb_listener" "https" {
  for_each = local.lb_map

  load_balancer_arn = aws_lb.main[each.key].arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.ui[each.key].arn
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${each.key}-https-listener"
  })
}

# HTTP Listeners (redirect to HTTPS)
resource "aws_lb_listener" "http" {
  for_each = local.lb_map

  load_balancer_arn = aws_lb.main[each.key].arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${each.key}-http-listener"
  })
}

# Listener Rules for API (path-based routing)
resource "aws_lb_listener_rule" "api" {
  for_each = local.lb_map

  listener_arn = aws_lb_listener.https[each.key].arn
  priority     = 100

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api[each.key].arn
  }

  condition {
    path_pattern {
      values = ["/api/*", "/api"]
    }
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${each.key}-api-rule"
  })
}

# Listener Rules for WebSocket (path-based routing) - only for ESS
resource "aws_lb_listener_rule" "websocket" {
  for_each = { for k, v in local.lb_map : k => v if v.enable_websocket }

  listener_arn = aws_lb_listener.https[each.key].arn
  priority     = 90

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api[each.key].arn
  }

  condition {
    path_pattern {
      values = ["/ws/*", "/ws"]
    }
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${each.key}-ws-rule"
  })
}

# Route 53 Records for ALBs
resource "aws_route53_record" "alb" {
  for_each = {
    for k, v in local.lb_map : k => v
    if v.enable_route53 && var.route53_zone_id != "" && v.domain != ""
  }

  zone_id = var.route53_zone_id
  name    = each.value.domain
  type    = "A"

  alias {
    name                   = aws_lb.main[each.key].dns_name
    zone_id                = aws_lb.main[each.key].zone_id
    evaluate_target_health = true
  }
}

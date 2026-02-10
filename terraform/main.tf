# VPC Module
module "vpc" {
  source = "./modules/vpc"

  project_name               = var.project_name
  region                     = var.region
  vpc_cidr                   = var.vpc_cidr
  availability_zones         = var.availability_zones
  public_subnet_cidrs        = var.public_subnet_cidrs
  network_subnet_cidrs       = var.network_subnet_cidrs
  app_subnet_cidrs           = var.app_subnet_cidrs
  data_subnet_cidrs          = var.data_subnet_cidrs
  lambda_subnet_cidrs        = var.lambda_subnet_cidrs
  enable_interface_endpoints = var.enable_interface_endpoints
}

# EC2 Module for Slack Bot
module "ec2_slack_bot" {
  source = "./modules/ec2"

  project_name            = var.project_name
  instance_type           = var.ec2_instance_type
  vpc_id                  = module.vpc.vpc_id
  subnet_id               = module.vpc.public_subnet_ids[0]
  key_name                = var.ec2_key_name
  create_key_pair         = var.ec2_create_key_pair
  allowed_ssh_cidr_blocks = var.ec2_allowed_ssh_cidr_blocks
  root_volume_size        = var.ec2_root_volume_size
}

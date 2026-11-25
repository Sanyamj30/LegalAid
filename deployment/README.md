# LegalAId Cloud Deployment Guide

This directory contains the production-ready infrastructure and configuration files to deploy LegalAId onto AWS ECS (or any OCI-compliant container cloud platform).

---

## 1. Prerequisites

Before initiating cloud deployment:
1. **AWS CLI:** Install and configure with permissions for ECS, ECR, EFS, and RDS (`aws configure`).
2. **Docker Engine:** For building and pushing multi-architecture container images.
3. **Domain Name & ACM Certificate:** For SSL/TLS termination on the Application Load Balancer.

---

## 2. Step-by-Step Deployment Procedure

### Step 1: Create Amazon Elastic Container Registry (ECR) Repositories
```bash
aws ecr create-repository --repository-name legalaid-backend --region ap-south-1
aws ecr create-repository --repository-name legalaid-mcp --region ap-south-1
```

### Step 2: Authenticate and Push Docker Images
```bash
# Log in to ECR
aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.ap-south-1.amazonaws.com

# Build and tag backend
docker build -t legalaid-backend -f backend/Dockerfile backend/
docker tag legalaid-backend:latest <ACCOUNT_ID>.dkr.ecr.ap-south-1.amazonaws.com/legalaid-backend:latest
docker push <ACCOUNT_ID>.dkr.ecr.ap-south-1.amazonaws.com/legalaid-backend:latest

# Build and tag FastMCP server
docker build -t legalaid-mcp -f backend/Dockerfile.mcp backend/
docker tag legalaid-mcp:latest <ACCOUNT_ID>.dkr.ecr.ap-south-1.amazonaws.com/legalaid-mcp:latest
docker push <ACCOUNT_ID>.dkr.ecr.ap-south-1.amazonaws.com/legalaid-mcp:latest
```

### Step 3: Register ECS Task Definition
```bash
aws ecs register-task-definition \
    --cli-input-json file://deployment/ecs_fargate_task_definition.json \
    --region ap-south-1
```

### Step 4: Create ECS Cluster and Service
```bash
aws ecs create-cluster --cluster-name legalaid-production --region ap-south-1

aws ecs create-service \
    --cluster legalaid-production \
    --service-name legalaid-service \
    --task-definition legalaid-multiagent-cluster \
    --desired-count 2 \
    --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={subnets=[subnet-12345,subnet-67890],securityGroups=[sg-abcdef],assignPublicIp=ENABLED}" \
    --region ap-south-1
```

---

## 3. Verifying Live Cloud Health

Once deployed behind the Application Load Balancer:
```bash
# Check public gateway health
curl -f https://<ALB_DNS_OR_DOMAIN>/api/system/health

# Check MCP tool service health
curl -f https://<ALB_DNS_OR_DOMAIN>/mcp/health
```


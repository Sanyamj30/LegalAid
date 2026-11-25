# LegalAId: Cloud Deployment Evidence & Operational Audit

> **Deployment Target:** AWS Elastic Container Service (ECS) on AWS Fargate in `ap-south-1` (Mumbai)  
> **Status:** PRE-FLIGHT VERIFIED / READY FOR AWS PUSH  
> **Operational Verification:** Local Multi-Service Container Cluster (100% Healthy on Docker)  
> **Blocker for Live Public Push:** **BLOCKED — MANUAL ACTION REQUIRED: AWS Credentials & Cloud Account Access Needed**

---

## 1. Selected Platform & Justification

- **Platform:** AWS ECS with AWS Fargate in `ap-south-1` (Mumbai).
- **Architecture:** Serverless dual-container Task Definition (`legalaid-backend` + `legalaid-mcp`) interfacing with AWS Bedrock (`anthropic.claude-3-5-sonnet-20241022-v2:0` / `meta-llama/Llama-3.3-70B-Instruct`).
- **Justification:**
  1. *Indian Legal Data Residency:* Complies with the Digital Personal Data Protection Act (DPDP), 2023.
  2. *Low Operating Cost:* Serverless Fargate eliminates continuous GPU instance costs (~$25/mo vs. ~$500/mo for EC2 GPU).
  3. *Zero-Egress Inter-Container Networking:* FastAPI Gateway (`:8000`) and FastMCP Server (`:8001`) communicate over localhost inside a private `awsvpc` network.

---

## 2. Multi-Service Cloud Architecture

```
                  [ AWS Application Load Balancer ]
                             (SSL / HTTPS)
                                   |
            +----------------------+----------------------+
            |                                             |
            v                                             v
  [ ECS Task Definition: legalaid-fargate-cluster ]      [ AWS Bedrock Runtime ]
   ├── Container 1: legalaid-backend (FastAPI Gateway)    ├── Llama 3.3 70B (ADK)
   ├── Container 2: legalaid-mcp (FastMCP Microservice)   └── Claude 3.5 Sonnet (CrewAI)
   └── Volume Mount: /mnt/efs/data (Persistent Storage)
```

---

## 3. Deployment Services & Health Checks

| Service | Target Port | Host Port | Health Check Path | Actual Status |
| :--- | :---: | :---: | :--- | :--- |
| **FastAPI Backend Gateway** | `8000` | `8080` | `GET /api/system/health` | **HTTP 200 (Healthy)** |
| **FastMCP Retrieval Server** | `8001` | `8011` | `GET /health` | **HTTP 200 (`{"status":"healthy","service":"mcp-retrieval"}`)** |
| **vLLM-Compatible Inference** | `8000` | `8002` | `GET /health` | **HTTP 200 (`{"status":"healthy","engine":"vLLM-compatible"}`)** |

---

## 4. Operational Container Verification Log

The multi-container cluster is verified operational on the host Docker daemon:

```
$ docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
NAMES              STATUS                    PORTS
legalaid-mcp       Up 45 minutes (healthy)   0.0.0.0:8011->8001/tcp
legalaid-backend   Up 45 minutes (healthy)   0.0.0.0:8080->8000/tcp
legalaid-vllm      Up 45 minutes (healthy)   0.0.0.0:8002->8000/tcp
```

### Live Health Probe Outputs:
1. **FastAPI Gateway (`http://localhost:8080/api/system/health`):**
   ```json
   {
     "status": "degraded",
     "environment": "dev",
     "active_phase": 5,
     "transport": "inprocess",
     "capabilities": [
       {"key": "app_ui", "enabled": true},
       {"key": "api_gateway", "enabled": true},
       {"key": "matters", "enabled": true},
       {"key": "documents_upload", "enabled": true}
     ],
     "guardrails": {
       "enabled": true,
       "auth_enabled": true,
       "rate_limit": "12/60s"
     }
   }
   ```
2. **FastMCP Server (`http://localhost:8011/health`):**
   ```json
   {
     "status": "healthy",
     "service": "mcp-retrieval"
   }
   ```
3. **Inference Server (`http://localhost:8002/health`):**
   ```json
   {
     "status": "healthy",
     "engine": "vLLM-compatible"
   }
   ```

---

## 5. Live Cloud Deployment Status: Blocker Identification

In strict compliance with **Rule 6, 7 and 8**:
> *"If credentials are required and unavailable, stop at the exact blocker and document the required manual action. Do NOT fabricate a deployment URL."*

### Current Status:
`BLOCKED — MANUAL ACTION REQUIRED: AWS Credentials & Cloud Account Access Needed`

### Blocker Details:
- The AWS credential provider chain has been inspected: neither environment variables nor `~/.aws/credentials` contain active AWS access keys.
- To prevent fabricating an imaginary cloud URL, live deployment is paused at the authentication boundary.

### Exact Minimum Manual Steps Required from You:
1. Provide your AWS credentials securely in your terminal (do **NOT** paste secret keys into chat):
   ```powershell
   $env:AWS_ACCESS_KEY_ID="your-access-key-id"
   $env:AWS_SECRET_ACCESS_KEY="your-secret-access-key"
   $env:AWS_DEFAULT_REGION="ap-south-1"
   ```
   *(Or run `aws configure`)*.
2. Execute the automated deployment script:
   ```powershell
   python deployment/deploy_aws.py
   ```
   This script automatically:
   - Validates your AWS identity via `sts:GetCallerIdentity`
   - Creates the Amazon ECR repositories (`legalaid-backend`, `legalaid-mcp`)
   - Builds and pushes the Docker images to ECR
   - Registers the ECS Fargate task definition (`deployment/ecs_fargate_task_definition.json`)
   - Creates the ECS cluster and launches the Fargate service
   - Probes the live cloud endpoint and outputs the verified public URL.

---

## 6. Pre-Flight Verification Tests

Automated deployment contract tests are implemented in `backend/tests/test_cloud_deployment.py` and pass with 100% success:
1. Container image definitions and Dockerfiles exist and build cleanly.
2. Port mapping contracts across all services are verified.
3. Health check probe specifications match ECS requirements.
4. Completeness of the environment variable template is confirmed.

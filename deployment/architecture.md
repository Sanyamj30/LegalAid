# LegalAId: Enterprise Cloud Deployment Architecture

> **Target Cloud Platform:** AWS Elastic Container Service (ECS) with AWS Fargate / EC2 in `ap-south-1` (Mumbai)  
> **Storage & Database:** AWS Aurora PostgreSQL Serverless v2 + AWS Elastic File System (EFS) for Vector Indices  
> **LLM Runtime:** AWS Bedrock (Claude 3.5 Sonnet / Llama 3.3 70B) or dedicated GPU EC2 instance (g5.2xlarge)  
> **Deployment Status:** Pre-flight Containerized Verification Complete; Live AWS Push Blocked Pending Cloud Credentials

---

## 1. Why AWS ECS in ap-south-1 (Mumbai) Was Selected

1. **Indian Sovereign Data Residency Compliance:**
   Legal research platforms handling confidential petitions, FIRs, and corporate litigation files must strictly adhere to Indian data localization principles (Digital Personal Data Protection Act, 2023). Deploying in `ap-south-1` (Mumbai) ensures all client communications, queries, and retrieved gazettes remain within sovereign Indian borders.

2. **Native Support for Multi-Container Tasks (Sidecar Pattern):**
   LegalAId requires simultaneous execution of:
   - The FastAPI web gateway (`legalaid-backend`)
   - The FastMCP Retrieval Server (`legalaid-mcp`)
   - The local/hybrid LLM inference engine (`legalaid-vllm`)
   AWS ECS supports co-locating these containers in a single `awsvpc` Task Definition, enabling low-latency localhost inter-service communication over private ports without public internet exposure.

3. **High-Performance Shared Persistent Storage (EFS):**
   The hybrid retrieval engine utilizes pre-built FAISS dense vector indices and BM25 index files (`/mnt/efs/indices`). AWS EFS allows multi-AZ mount points across auto-scaled ECS tasks, avoiding index duplication across container images.

---

## 2. Cloud Architecture Topology

```
                  [ Public Internet / User HTTPS Requests ]
                                     |
                                     v
                       [ AWS Application Load Balancer ]
                             (Port 443 with ACM SSL)
                                     |
                 +-------------------+-------------------+
                 |                                       |
                 v (Path: /api/*)                        v (Path: /mcp/*)
     [ Private Subnet: ap-south-1a ]         [ Private Subnet: ap-south-1b ]
  +-------------------------------------+ +-------------------------------------+
  | ECS Task (legalaid-multiagent)     | | ECS Task (Auto-scaled Replica)      |
  |  - Container 1: legalaid-backend    | |  - Container 1: legalaid-backend    |
  |    (FastAPI Gateway :8000)         | |    (FastAPI Gateway :8000)         |
  |  - Container 2: legalaid-mcp        | |  - Container 2: legalaid-mcp        |
  |    (FastMCP Server :8001)          | |    (FastMCP Server :8001)          |
  +-------------------------------------+ +-------------------------------------+
                 |                                       |
                 +-------------------+-------------------+
                                     |
                 +-------------------+-------------------+
                 |                                       |
                 v                                       v
    [ AWS EFS Persistent Volume ]           [ AWS Aurora PostgreSQL Serverless ]
       (FAISS & BM25 Indices)                     (26 Relational Tables)
```

---

## 3. Container Services & Port Mapping

| Container Name | Base Image | Port | Role | Health Check |
| :--- | :--- | :---: | :--- | :--- |
| `legalaid-backend` | `python:3.11-slim` | `8000` | FastAPI Gateway, A2A Broker, Blackboard | `GET /api/system/health` |
| `legalaid-mcp` | `python:3.11-slim` | `8001` | FastMCP Retrieval Service (FAISS+BM25) | `GET /health` |
| `legalaid-vllm` | `vllm/vllm-openai` | `8000` | High-throughput local inference | `GET /health` |

---

## 4. Cloud Operating Cost Estimate

| AWS Service | Instance / Tier | Unit Cost (ap-south-1) | Monthly Cost |
| :--- | :--- | :--- | :--- |
| **ECS Fargate (Backend + MCP)** | 2 vCPU, 4GB RAM | \$0.04048 / vCPU-hr + \$0.004445 / GB-hr | ~\$72.00 |
| **AWS Application Load Balancer** | 1 ALB (1 LCU) | \$0.0225 / hr + \$0.008 / LCU-hr | ~\$22.00 |
| **Aurora PostgreSQL Serverless v2** | 0.5 - 2 ACUs | \$0.12 / ACU-hr | ~\$43.00 |
| **Amazon EFS (Corpus & Indices)** | 10 GB General Purpose | \$0.30 / GB-month | ~\$3.00 |
| **AWS Bedrock / Inference API** | Pay-as-you-go | \$0.003 / 1k tokens | ~\$30.00 (10k queries) |
| **Total Estimated Operating Cost**| | | **~\$170.00 / month** |


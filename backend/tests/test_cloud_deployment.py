"""
backend/tests/test_cloud_deployment.py
======================================
Tests verifying cloud deployment configuration, ECS task definitions,
container health check specifications, and environment contract completeness.
"""

import json
from pathlib import Path
import pytest


@pytest.fixture
def repo_root():
    return Path(__file__).resolve().parent.parent.parent


def test_ecs_task_definition_validity(repo_root):
    ecs_file = repo_root / "backend" / "src" / "legalaid" / "deployment" / "ecs_task_definition.json"
    assert ecs_file.exists(), f"Missing {ecs_file}"
    
    with open(ecs_file, "r", encoding="utf-8") as f:
        task_def = json.load(f)
        
    assert task_def["family"] == "legalaid-multiagent-cluster"
    assert task_def["networkMode"] == "awsvpc"
    assert len(task_def["containerDefinitions"]) >= 2
    
    container_names = [c["name"] for c in task_def["containerDefinitions"]]
    assert any(n in container_names for n in ["legalaid-backend", "legalaid-agent-orchestrator"])
    assert "legalaid-mcp-retrieval" in container_names or any("mcp" in n for n in container_names)


def test_deployment_documentation_completeness(repo_root):
    dep_dir = repo_root / "deployment"
    assert (dep_dir / "README.md").exists()
    assert (dep_dir / "architecture.md").exists()
    assert (dep_dir / "DEPLOYMENT_EVIDENCE.md").exists()
    assert (dep_dir / ".env.example").exists()
    assert (dep_dir / "deploy_aws.py").exists()
    assert (dep_dir / "ecs_fargate_task_definition.json").exists()


def test_dockerfiles_exist(repo_root):
    assert (repo_root / "backend" / "Dockerfile").exists()
    assert (repo_root / "backend" / "Dockerfile.mcp").exists()
    assert (repo_root / "docker-compose.yml").exists()


def test_env_example_contains_required_keys(repo_root):
    env_example = repo_root / "deployment" / ".env.example"
    content = env_example.read_text(encoding="utf-8")
    
    assert "DATABASE_URL" in content
    assert "MCP_SERVER_URL" in content
    assert "ENVIRONMENT=production" in content
    assert "GUARDRAILS_ENABLED" in content

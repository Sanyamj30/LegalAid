# =============================================================================
# LegalAId Multi-Agent Orchestration Backend
# Production Container Specification (Python 3.11-slim)
# =============================================================================

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Install curl for container health checks and minimal build tools
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl build-essential \
    && rm -rf /var/lib/apt/lists/*

# Dependency layer caching
COPY pyproject.toml requirements.txt /app/

# Install locked dependencies (excluding linux GPU vllm wheel which runs in separate container)
RUN pip install --upgrade pip setuptools wheel \
    && pip install --no-cache-dir \
        fastapi==0.141.1 \
        uvicorn[standard]==0.53.0 \
        pydantic==2.13.5 \
        pydantic-settings==2.16.1 \
        sqlalchemy==2.1.6 \
        httpx==0.28.1 \
        fastmcp>=0.4.0 \
        mcp>=1.3.0 \
        rank-bm25==0.2.2 \
        numpy>=2.0.0 \
        scikit-learn>=1.4.0 \
        scipy>=1.11.0 \
        pymupdf==1.28.2 \
        python-docx==1.2.0 \
        python-multipart==0.0.32 \
        pyyaml==6.0.3 \
        structlog==26.1.0 \
        pyjwt==2.15.1 \
        sse-starlette==3.6.0 \
        faiss-cpu>=1.10.0 \
    && apt-get purge -y --auto-remove build-essential

# Copy source code and default prompts
COPY src /app/src

# Persistent volume mounts for database, indexes, and document uploads
RUN mkdir -p /data/uploads /data/indexes /data/knowledge_sources

# Run as non-root user for security compliance
RUN useradd --create-home --uid 10001 legalaid \
    && chown -R legalaid:legalaid /app /data

USER legalaid

EXPOSE 8000

# Native health check against root health endpoint
HEALTHCHECK --interval=10s --timeout=5s --retries=3 --start-period=10s \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "legalaid.services.gateway.app:app", "--host", "0.0.0.0", "--port", "8000"]

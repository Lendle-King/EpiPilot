# Minimal import/synthetic-smoke environment, NOT native full ML dependency parity.
FROM python:3.11-slim@sha256:db20d8506e2f15b6db39b40b6015ca46bae855dec6dd326d462cfe79ae972893 AS aide
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/mpl
RUN --mount=type=cache,target=/root/.cache/pip pip install --timeout=120 --retries=2 pydantic==2.12.5 numpy==1.26.2 pandas==2.1.4 \
    black==26.3.1 funcy==2.0 humanize==4.8.0 jsonschema==4.19.2 \
    openai==1.109.1 anthropic==0.86.0 rich==13.7.0 dataclasses-json==0.6.7 \
    omegaconf==2.3.0 loguru==0.7.2 shutup==0.2.0 tqdm==4.66.3 coolname==2.2.0 \
    igraph==0.11.8 genson==1.3.0 backoff==2.2.1 google-genai==1.10.0 \
    hydra-core==1.3.2 jsonlines==4.0.0 colorama==0.4.6 wandb==0.19.9 \
    jinja2==3.1.6 python-dotenv==1.1.0 psutil==7.0.0
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN pip freeze --all > /environment.txt
USER 65534:65534
WORKDIR /work

# Separate image: native AIRA pins conflict with AIDE. Never unify them silently.
FROM aide AS aira
USER root
RUN --mount=type=cache,target=/root/.cache/pip pip install --timeout=120 --retries=2 \
    jsonschema==4.23.0 numpy==2.2.4 pandas==2.2.3 rich==14.0.0 humanize==4.12.2 \
    openai==1.72.0 anthropic==0.49.0 litellm==1.65.7
RUN pip freeze --all > /environment.txt
USER 65534:65534

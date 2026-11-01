#ARG PYTHON_VERSION=3.12-slim-bullseye
#FROM python:${PYTHON_VERSION}

#WORKDIR /app

#RUN apt-get update && \
#    apt-get install -y bash ca-certificates curl git libexpat1 openssh-client ripgrep && \
#    rm -rf /var/lib/apt/lists/*
#RUN git config --global --add safe.directory '*'
#RUN git config --global core.quotepath false

#ARG AI_REVIEW_VERSION
#RUN pip install --no-cache-dir xai-review==${AI_REVIEW_VERSION}

# Stage 1: сборка с Poetry
FROM python:3.12-alpine AS builder
RUN pip install --no-cache-dir poetry==2.4.1
WORKDIR /app
COPY . .
RUN poetry config virtualenvs.in-project true \
    && poetry install --only main

# Stage 2: финальный образ
FROM python:3.12-alpine
RUN apk add --no-cache bash ca-certificates curl git openssh-client ripgrep
RUN git config --global --add safe.directory '*'
RUN git config --global core.quotepath false

WORKDIR /app
COPY --from=builder /app/.venv .venv
COPY --from=builder /app/ai_review ./ai_review

# Копируем вспомогательные скрипты (запускаются из GitLab CI)
COPY --from=builder /app/scripts ./scripts
RUN chmod +x /app/scripts/*.py

ENV PATH="/app/.venv/bin:/app/scripts:$PATH"
CMD ["ai-review"]

# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS web-builder

WORKDIR /workspace/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
COPY tokens.css /workspace/tokens.css
RUN npm run build


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    MODEL_CACHE_DIR=/models \
    HOME=/home/trap \
    HF_HOME=/models/huggingface \
    XDG_CACHE_HOME=/models/.cache

WORKDIR /workspace
RUN addgroup --system trap \
    && adduser --system --ingroup trap --home /home/trap trap \
    && mkdir -p /home/trap /models/huggingface /models/.cache \
    && chown -R trap:trap /home/trap /models

COPY api/ /workspace/api/
RUN python -m pip install --upgrade pip && python -m pip install -e /workspace/api

COPY scripts/ /workspace/scripts/
COPY data/ /workspace/data/
COPY results/ /workspace/results/
COPY THIRD_PARTY_NOTICES.md /workspace/THIRD_PARTY_NOTICES.md
COPY third_party/ /workspace/third_party/
COPY --from=web-builder /workspace/web/dist /workspace/web/dist
RUN --mount=type=cache,target=/model-download-cache \
    python /workspace/scripts/fetch_models.py /model-download-cache \
    && rm -rf /models/embedding /models/reranker /models/models.json \
    && cp -a /model-download-cache/embedding /models/embedding \
    && cp -a /model-download-cache/reranker /models/reranker \
    && cp /model-download-cache/models.json /models/models.json \
    && chown -R trap:trap /workspace /models

USER trap
RUN python /workspace/scripts/prewarm_models.py

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["uvicorn", "api.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

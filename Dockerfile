# ---- frontend build ----
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund --loglevel=error
COPY frontend/ ./
RUN npm run build

# ---- runtime ----
FROM python:3.13-slim
ARG INSTALL_FINBERT=0
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/app/data FRONTEND_DIST=/app/frontend/dist HF_HOME=/app/data/hf
WORKDIR /app/backend
COPY backend/requirements.txt backend/requirements-finbert.txt ./
RUN pip install -r requirements.txt \
 && if [ "$INSTALL_FINBERT" = "1" ]; then pip install -r requirements-finbert.txt; fi
COPY backend/ ./
COPY config/ /app/config/
COPY --from=web /web/dist /app/frontend/dist
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/status', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# syntax=docker/dockerfile:1
# Construir desde la raíz del repositorio:
#   docker build -f deploy/docker/Dockerfile -t valkiria:0.5.0 .

# --- Etapa 1: construye el wheel a partir del código fuente ---
FROM python:3.12-slim AS builder
WORKDIR /build
RUN pip install --no-cache-dir "build>=1.2,<2"
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m build --wheel --outdir /dist

# --- Etapa 2: imagen de ejecución sin herramientas de build ---
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VALKIRIA_FRONTEND_DIR=/app/frontend
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /dist/ /tmp/dist/
# Incluye el driver de PostgreSQL para usar la base sintética.
RUN sh -c 'pip install "$(ls /tmp/dist/valkiria-*.whl)[postgres]"' && rm -rf /tmp/dist
# El frontend queda propiedad de root y solo lectura para el usuario de la aplicación.
COPY frontend ./frontend
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]
CMD ["uvicorn", "valkiria.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

# syntax=docker/dockerfile:1
# Construir desde la raíz del repositorio:
#   docker build -f deploy/docker/Dockerfile -t valkiria:0.5.0 .
# Con Chromium para ejecutar los scripts web contra la app sintética (HU-010):
#   docker build -f deploy/docker/Dockerfile --build-arg WITH_BROWSER=true -t valkiria:0.5.0-browser .

# --- Etapa 1: construye el wheel a partir del código fuente ---
FROM python:3.12-slim AS builder
WORKDIR /build
RUN pip install --no-cache-dir "build>=1.2,<2"
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m build --wheel --outdir /dist

# --- Etapa 2: imagen de ejecución sin herramientas de build ---
FROM python:3.12-slim
ARG WITH_BROWSER=false
ENV PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    XDG_CONFIG_HOME=/tmp/.config \
    XDG_CACHE_HOME=/tmp/.cache \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VALKIRIA_FRONTEND_DIR=/app/frontend
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /dist/ /tmp/dist/
# Incluye el driver de PostgreSQL para usar la base sintética; con WITH_BROWSER=true, también Playwright y Chromium
# (en una ruta de solo lectura: el contenedor corre con el sistema de archivos de solo lectura y /tmp como tmpfs).
RUN if [ "$WITH_BROWSER" = "true" ]; then \
      sh -c 'pip install "$(ls /tmp/dist/valkiria-*.whl)[postgres,e2e]"' && python -m playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*; \
    else \
      sh -c 'pip install "$(ls /tmp/dist/valkiria-*.whl)[postgres]"'; \
    fi && rm -rf /tmp/dist
# El frontend queda propiedad de root y solo lectura para el usuario de la aplicación.
COPY frontend ./frontend
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]
CMD ["uvicorn", "valkiria.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

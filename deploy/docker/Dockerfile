FROM python:3.12-slim
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["uvicorn","valkiria.api.app:create_app","--factory","--host","0.0.0.0","--port","8000"]

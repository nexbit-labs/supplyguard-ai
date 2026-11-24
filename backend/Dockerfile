FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SG_SCHEMA_DIR=/app/ai/schemas

# Do not run as root inside the container. Create the user first so /app is writable by it.
RUN useradd --create-home --uid 1000 app
WORKDIR /app
RUN chown app:app /app

# Build context is the repo root (see docker-compose.yml) so ai/schemas can be copied in.
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app backend/. .
# ai/ python package (schema registry) + schemas; eval/fixtures/tests stay out of the image.
COPY --chown=app:app ai/__init__.py ai/schema_registry.py /app/ai/
COPY --chown=app:app ai/schemas /app/ai/schemas
USER app

EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]

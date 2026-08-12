FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic.ini ./
COPY synthetic_documents/.gitkeep ./synthetic_documents/.gitkeep

RUN python -m pip install --no-cache-dir .

RUN addgroup --system controltower \
    && adduser --system --ingroup controltower controltower \
    && chown -R controltower:controltower /app

USER controltower

EXPOSE 8000 8010

CMD ["control-tower-web", "--host", "0.0.0.0", "--port", "8000"]

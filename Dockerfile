FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir .

EXPOSE 8000 8010

CMD ["control-tower-web", "--host", "0.0.0.0", "--port", "8000"]

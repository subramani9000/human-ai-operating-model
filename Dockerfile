FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HAO_HOME=/app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[postgres]"

COPY policy ./policy
COPY data ./data
RUN hao policy validate && hao generate --sample 0

EXPOSE 8001
CMD ["hao", "mock", "serve", "--port", "8001"]

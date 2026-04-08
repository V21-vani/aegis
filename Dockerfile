FROM python:3.11-slim

LABEL maintainer="aegis-red"
LABEL description="Aegis-Red OpenEnv red-teaming environment"

WORKDIR /app

# Install dependencies first for Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY __init__.py .
COPY environment/ ./environment/
COPY tasks/ ./tasks/
COPY server/ ./server/
COPY inference.py .
COPY client.py .
COPY openenv.yaml .
COPY pyproject.toml .

EXPOSE 7860

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "environment.env:app", "--host", "0.0.0.0", "--port", "7860"]

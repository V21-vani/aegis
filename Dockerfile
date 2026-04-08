FROM python:3.11-slim

LABEL maintainer="aegis-red"
LABEL description="Aegis-Red OpenEnv red-teaming environment"

WORKDIR /app

# Install dependencies first for Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all application code
# .dockerignore handles excluding unnecessary files
COPY . .

EXPOSE 7860

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["uvicorn", "environment.env:app", "--host", "0.0.0.0", "--port", "7860"]

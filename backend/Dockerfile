FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# git is needed to clone/push in the sandbox; docker CLI is only needed if SANDBOX_MODE=docker
ARG INSTALL_DOCKER_CLI=false
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && if [ "$INSTALL_DOCKER_CLI" = "true" ]; then apt-get install -y --no-install-recommends docker.io; fi \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN useradd -m appuser
USER appuser
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')" || exit 1
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

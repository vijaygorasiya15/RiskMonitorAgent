# AgentCore Runtime requires linux/arm64; the same image also runs locally.
# Build for AgentCore:  docker buildx build --platform linux/arm64 -t risk-monitor .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY prompts ./prompts
COPY config.yaml tenants.example.yaml ./

# run as non-root
RUN useradd --create-home --uid 10001 agent && chown -R agent /app
USER agent

EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/ping', timeout=2).status==200 else 1)"

CMD ["python", "-m", "app.main"]

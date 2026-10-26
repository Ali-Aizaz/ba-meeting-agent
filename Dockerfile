FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY data/knowledge ./data/knowledge
RUN pip install --no-cache-dir . \
    && python -m playwright install chromium

COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV PYTHONUNBUFFERED=1
ENV BA_BROWSER__HEADLESS=false
ENV BA_BROWSER__PROFILE_DIR=/app/data/browser-profile

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["join"]

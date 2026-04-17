FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SCREENSHOT_PLATFORM_PROFILE=auto \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        fastapi==0.116.1 \
        uvicorn==0.35.0 \
        playwright==1.55.0

RUN playwright install --with-deps chromium

COPY docker/browser_screenshot_service/app.py /app/app.py

EXPOSE 4300

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "4300"]

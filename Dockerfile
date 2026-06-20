# Prebuilt image already has Chromium + all OS deps for Playwright.
# This avoids slow/expensive apt-get installs on every Railway build.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

# Install python deps first (cached layer if requirements.txt doesn't change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chromium browser binaries are already baked into this base image,
# so we do NOT run `playwright install` again (saves build time/credits).

COPY app ./app

ENV PYTHONUNBUFFERED=1
ENV PORT=8000

EXPOSE 8000

# Shell form so $PORT (set by Railway at runtime) actually expands
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}

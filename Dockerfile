FROM node:24-slim AS web-build
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"
ENV CAREERPILOT_WEB_ROOT=/opt/careerpilot-web \
    LANGCHAIN_TRACING_V2=false \
    LANGSMITH_TRACING=false

WORKDIR /workspace
RUN python -m venv /opt/venv
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && printf 'Acquire::Retries "3";\n' > /etc/apt/apt.conf.d/80-retries \
    && apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-chi-sim ffmpeg pocketsphinx pocketsphinx-en-us \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
COPY . .
COPY --from=web-build /web/dist /opt/careerpilot-web
RUN pip install --no-cache-dir --no-deps -e .

# Named volumes inherit ownership on first creation. Source is mounted read-only.
RUN groupadd --gid 10001 careerpilot \
    && useradd --uid 10001 --gid careerpilot --create-home careerpilot \
    && mkdir -p /var/lib/careerpilot \
    && chown careerpilot:careerpilot /var/lib/careerpilot
USER careerpilot
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "careerpilot.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

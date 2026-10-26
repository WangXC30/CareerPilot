FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

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
RUN pip install --no-cache-dir --no-deps -e .

# Named volumes inherit ownership on first creation. Source is mounted read-only.
RUN groupadd --gid 10001 careerpilot \
    && useradd --uid 10001 --gid careerpilot --create-home careerpilot \
    && mkdir -p /var/lib/careerpilot \
    && chown careerpilot:careerpilot /var/lib/careerpilot
USER careerpilot
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "careerpilot.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

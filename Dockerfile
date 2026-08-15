FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8010
WORKDIR /app

RUN addgroup --system securequote && adduser --system --ingroup securequote securequote
COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt
COPY applications ./applications
COPY src ./src
COPY scripts ./scripts
RUN mkdir -p /app/applications/securequote_lite/logs && chown -R securequote:securequote /app

USER securequote
EXPOSE 8010
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/securequote/health', timeout=3)"
CMD ["sh", "-c", "uvicorn applications.securequote_lite.app:app --host 0.0.0.0 --port ${PORT} --workers 1"]

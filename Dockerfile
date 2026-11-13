FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PHOTO_ROOT=/photos \
    DATA_DIR=/data \
    PORT=8080 \
    UMASK=000

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 前端已编译好放在 rawpick/static/js，构建镜像不需要 Node
COPY rawpick ./rawpick

EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)"

CMD ["python", "-m", "rawpick"]

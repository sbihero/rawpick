# 第一阶段只用来取中文字体（给客户相册的水印用），最终镜像里只留一个字体文件（约 20MB）
FROM python:3.13-slim AS fonts
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-noto-cjk \
 && rm -rf /var/lib/apt/lists/*

FROM python:3.13-slim

LABEL org.opencontainers.image.title="rawpick" \
      org.opencontainers.image.source="https://github.com/sbihero/rawpick"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PHOTO_ROOT=/photos \
    DATA_DIR=/data \
    PORT=8080 \
    CLIENT_PORT=8081 \
    WATERMARK_FONT=/app/fonts/NotoSansCJK-Bold.ttc \
    UMASK=000

WORKDIR /app
COPY --from=fonts /usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc /app/fonts/
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 前端已编译好放在 rawpick/static/js，构建镜像不需要 Node
COPY rawpick ./rawpick

# 8080：管理员端，只在局域网用；8081：客户端（客户相册），可以通过隧道对外
EXPOSE 8080 8081
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)"

CMD ["python", "-m", "rawpick"]

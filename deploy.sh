#!/bin/bash
# 在 Unraid 终端里运行：构建镜像，替换旧容器，启动新容器。
# 用法：bash deploy.sh
# 设置写在同目录的 deploy.env（从 deploy.env.example 复制）。
set -euo pipefail

cd "$(dirname "$0")"

# 默认值，可被 deploy.env 覆盖
PHOTO_ROOT="/mnt/user/photos"
APPDATA="/mnt/user/appdata/rawpick"
PORT=8080
PREVIEW_WORKERS=4
PUID=99
PGID=100
UMASK=000
RAW_EXTS=""
CONTAINER_NAME=rawpick
IMAGE_NAME=rawpick

if [[ -f deploy.env ]]; then
  # 去掉 Windows 记事本可能带进来的 \r
  # shellcheck disable=SC1090
  source <(tr -d '\r' < deploy.env)
else
  echo "提示：没有找到 deploy.env，使用默认设置。建议先执行 cp deploy.env.example deploy.env 并修改。"
fi

fail() { echo "错误：$*" >&2; exit 1; }

command -v docker >/dev/null || fail "找不到 docker 命令。请在 Unraid 终端里运行，并确认 Docker 服务已启用。"
[[ -d "$PHOTO_ROOT" ]] || fail "照片根目录不存在：$PHOTO_ROOT（请修改 deploy.env 里的 PHOTO_ROOT）"

echo "==> 照片根目录：$PHOTO_ROOT"
echo "==> 数据目录：  $APPDATA"
mkdir -p "$APPDATA"
chown "$PUID:$PGID" "$APPDATA"

OLD_IMAGE=$(docker image inspect -f '{{.Id}}' "$IMAGE_NAME" 2>/dev/null || true)

echo "==> 构建镜像 $IMAGE_NAME"
docker build -t "$IMAGE_NAME" .
NEW_IMAGE=$(docker image inspect -f '{{.Id}}' "$IMAGE_NAME")

if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  echo "==> 停止并删除旧容器 $CONTAINER_NAME"
  docker rm -f "$CONTAINER_NAME" >/dev/null
fi

echo "==> 启动容器 $CONTAINER_NAME"
docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  --user "$PUID:$PGID" \
  -p "$PORT:8080" \
  -v "$PHOTO_ROOT:/photos" \
  -v "$APPDATA:/data" \
  -e UMASK="$UMASK" \
  -e PREVIEW_WORKERS="$PREVIEW_WORKERS" \
  -e RAW_EXTS="$RAW_EXTS" \
  --label "net.unraid.docker.webui=http://[IP]:[PORT:8080]/" \
  "$IMAGE_NAME" >/dev/null

# 删掉被替换下来的旧镜像（只删 rawpick 自己的）
if [[ -n "$OLD_IMAGE" && "$OLD_IMAGE" != "$NEW_IMAGE" ]]; then
  docker rmi "$OLD_IMAGE" >/dev/null 2>&1 || true
fi

echo -n "==> 等待服务就绪"
for _ in $(seq 1 30); do
  if docker exec "$CONTAINER_NAME" python -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2)" \
      >/dev/null 2>&1; then
    echo " 完成"
    IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    echo "==> 打开 http://${IP:-<Unraid IP>}:$PORT"
    exit 0
  fi
  echo -n "."
  sleep 1
done
echo
echo "服务 30 秒内没有就绪，最近的日志："
docker logs --tail 50 "$CONTAINER_NAME"
exit 1

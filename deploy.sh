#!/bin/bash
# rawpick 在 Unraid 上的部署脚本。设置写在同目录的 deploy.env（从 deploy.env.example 复制）。
#
#   bash deploy.sh               命令行方式：构建镜像，并用 docker run 启动（或替换）容器
#   bash deploy.sh --build-only  模板方式：只构建本地镜像、准备数据目录、安装 Unraid 模板；
#                                容器由 Unraid Docker 页面的 Add Container / Edit 创建和更新
#   bash deploy.sh --help        显示帮助
#
# 两种方式二选一，详见 docs/deployment.md。
set -euo pipefail

cd "$(dirname "$0")"

usage() { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; }

MODE=run
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --build-only) MODE=build ;;
    --force) FORCE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$arg" >&2; usage; exit 2 ;;
  esac
done

# 默认值，可被 deploy.env 覆盖
PHOTO_ROOT="/mnt/user/photos"
APPDATA="/mnt/user/appdata/rawpick"
PORT=8080
CLIENT_PORT=8081
PREVIEW_WORKERS=4
PUID=99
PGID=100
UMASK=000
RAW_EXTS=""
CONTAINER_NAME=rawpick
IMAGE_NAME=rawpick
TEMPLATE_DIR="${UNRAID_TEMPLATE_DIR:-/boot/config/plugins/dockerMan/templates-user}"

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

# 已有同名容器是不是 Unraid 模板创建的（dockerman 会打这个标签）
EXISTING=""
if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  if [[ "$(docker container inspect -f '{{index .Config.Labels "net.unraid.docker.managed"}}' "$CONTAINER_NAME" 2>/dev/null)" == "dockerman" ]]; then
    EXISTING=template
  else
    EXISTING=cli
  fi
fi

if [[ "$MODE" == run && "$EXISTING" == template && "$FORCE" != 1 ]]; then
  fail "容器 $CONTAINER_NAME 是用 Unraid 模板创建的。
  更新请用：bash deploy.sh --build-only，然后在 Docker 页面点 $CONTAINER_NAME → Edit → Apply。
  如果确实要改回命令行方式，加 --force（会删除现有容器，模板文件保留）。"
fi

echo "==> 照片根目录：$PHOTO_ROOT"
echo "==> 数据目录：  $APPDATA"
mkdir -p "$APPDATA"
chown "$PUID:$PGID" "$APPDATA"

OLD_IMAGE=$(docker image inspect -f '{{.Id}}' "$IMAGE_NAME" 2>/dev/null || true)

echo "==> 构建镜像 $IMAGE_NAME"
docker build -t "$IMAGE_NAME" .
NEW_IMAGE=$(docker image inspect -f '{{.Id}}' "$IMAGE_NAME")

# 清理没有被任何容器使用的旧 rawpick 镜像（只动带 rawpick 标签的）
cleanup_images() {
  docker image prune -f --filter "label=org.opencontainers.image.title=rawpick" >/dev/null 2>&1 || true
}

# ----------------------------------------------------------------- 模板方式
if [[ "$MODE" == build ]]; then
  cleanup_images

  xml_escape() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g'; }
  sed_escape() { printf '%s' "$1" | sed -e 's/[\\|&]/\\&/g'; }
  val() { sed_escape "$(xml_escape "$1")"; }

  TEMPLATE_FILE="$TEMPLATE_DIR/my-$CONTAINER_NAME.xml"
  if [[ ! -d "$TEMPLATE_DIR" ]]; then
    echo "==> 没有找到 Unraid 模板目录 $TEMPLATE_DIR，跳过模板安装"
  elif [[ -f "$TEMPLATE_FILE" ]]; then
    echo "==> 模板已存在，保留你的设置：$TEMPLATE_FILE"
    if ! grep -q 'Target="8081"' "$TEMPLATE_FILE"; then
      echo
      echo "注意：0.3.0 起多了一个客户端端口（容器内 8081，给客户相册用），你的模板里还没有。"
      echo "  在 Docker 页面点 $CONTAINER_NAME → Edit → 页面底部 Add another Path, Port, Variable, Label or Device："
      echo "  Config Type 选 Port，Name 填 客户端端口，Container Port 填 8081，Host Port 填 $CLIENT_PORT，Connection Type 选 TCP → Add → Apply。"
      echo "  不加也能用，只是客户相册打不开。"
    fi
  else
    sed -e "s|<Name>rawpick</Name>|<Name>$(val "$CONTAINER_NAME")</Name>|" \
        -e "s|<Repository>rawpick</Repository>|<Repository>$(val "$IMAGE_NAME")</Repository>|" \
        -e "s|<ExtraParams>--user 99:100</ExtraParams>|<ExtraParams>--user $(val "$PUID:$PGID")</ExtraParams>|" \
        -e "/Target=\"8080\"/s|>8080</Config>|>$(val "$PORT")</Config>|" \
        -e "/Target=\"8081\"/s|>8081</Config>|>$(val "$CLIENT_PORT")</Config>|" \
        -e "s|>/mnt/user/photos</Config>|>$(val "$PHOTO_ROOT")</Config>|" \
        -e "s|>/mnt/user/appdata/rawpick</Config>|>$(val "$APPDATA")</Config>|" \
        -e "/Target=\"PREVIEW_WORKERS\"/s|>4</Config>|>$(val "$PREVIEW_WORKERS")</Config>|" \
        -e "/Target=\"UMASK\"/s|>000</Config>|>$(val "$UMASK")</Config>|" \
        unraid/my-rawpick.xml > "$TEMPLATE_FILE"
    if [[ -n "$RAW_EXTS" ]]; then
      sed -i "/Target=\"RAW_EXTS\"/s|Mask=\"false\"/>|Mask=\"false\">$(val "$RAW_EXTS")</Config>|" "$TEMPLATE_FILE"
    fi
    echo "==> 已安装 Unraid 模板：$TEMPLATE_FILE"
  fi

  echo
  if [[ "$EXISTING" == template ]]; then
    echo "镜像已更新。到 Unraid 的 Docker 页面，点 $CONTAINER_NAME → Edit → Apply，容器就会换成新镜像。"
  else
    if [[ "$EXISTING" == cli ]]; then
      echo "注意：已有一个用命令行创建的容器 $CONTAINER_NAME，用模板创建前请先删除它："
      echo "    docker rm -f $CONTAINER_NAME"
      echo
    fi
    echo "下一步：到 Unraid 的 Docker 页面 → Add Container → Template 下拉菜单选 $CONTAINER_NAME → 检查设置 → Apply。"
  fi
  exit 0
fi

# ----------------------------------------------------------------- 命令行方式
if [[ -n "$EXISTING" ]]; then
  echo "==> 停止并删除旧容器 $CONTAINER_NAME"
  docker rm -f "$CONTAINER_NAME" >/dev/null
fi

echo "==> 启动容器 $CONTAINER_NAME"
docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  --user "$PUID:$PGID" \
  -p "$PORT:8080" \
  -p "$CLIENT_PORT:8081" \
  -v "$PHOTO_ROOT:/photos" \
  -v "$APPDATA:/data" \
  -e UMASK="$UMASK" \
  -e PREVIEW_WORKERS="$PREVIEW_WORKERS" \
  -e RAW_EXTS="$RAW_EXTS" \
  --label "net.unraid.docker.webui=http://[IP]:[PORT:8080]/" \
  "$IMAGE_NAME" >/dev/null

# 删掉被替换下来的旧镜像
if [[ -n "$OLD_IMAGE" && "$OLD_IMAGE" != "$NEW_IMAGE" ]]; then
  docker rmi "$OLD_IMAGE" >/dev/null 2>&1 || true
fi
cleanup_images

echo -n "==> 等待服务就绪"
for _ in $(seq 1 30); do
  if docker exec "$CONTAINER_NAME" python -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2)" \
      >/dev/null 2>&1; then
    echo " 完成"
    IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    echo "==> 打开 http://${IP:-<Unraid IP>}:$PORT"
    echo "==> 客户相册端口：$CLIENT_PORT"
    exit 0
  fi
  echo -n "."
  sleep 1
done
echo
echo "服务 30 秒内没有就绪，最近的日志："
docker logs --tail 50 "$CONTAINER_NAME"
exit 1

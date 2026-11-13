# 部署指南（Unraid）

rawpick 以单个 Docker 容器运行在 Unraid 上。镜像直接在 Unraid 本机构建，不需要镜像仓库，Windows 上也不用装 Python 或 Docker。

容器有两种创建方式，**二选一**：

| | 模板方式（推荐） | 命令行方式 |
| --- | --- | --- |
| 构建镜像 | `bash deploy.sh --build-only` | `bash deploy.sh`（构建 + 启动一步完成） |
| 创建容器 | Docker 页面 → Add Container → 选 rawpick 模板 | 脚本里的 `docker run` |
| 在 Docker 页面编辑配置 | 可以 | 不可以，要改 `deploy.env` 再运行脚本 |
| 更新 | 拉代码 → `--build-only` → Docker 页面 Edit → Apply | 拉代码 → `bash deploy.sh` |
| 适合 | 自己的 Unraid | 不用 Unraid 模板的环境，或想一条命令搞定 |

## 前提

- Unraid 已启用 Docker：Settings → Docker → Enable Docker: Yes。
- Unraid 能访问外网：第一次构建要下载 Python 基础镜像和几个 Python 包。
- 照片按约定存放：照片根目录下每个出门目录里都有 `jpg/` 和 `raw/`。

## 第一步：把代码放到 Unraid 上

下面两种方式任选一种。

### 方式 A：在 Windows 上 clone 到共享里

不需要在 Unraid 上装任何东西。

1. 在 Windows 上安装 [GitHub Desktop](https://desktop.github.com/)。
2. File → Clone repository → URL 填 `https://github.com/sbihero/rawpick`。Local path 选一个映射到 Unraid 共享的目录，
   比如 `Z:\apps\rawpick`（`Z:` 映射的是 `\\unraid\<共享>`）。
3. 记下它在 Unraid 上对应的路径，比如 `/mnt/user/<共享>/apps/rawpick`。

仓库里的 `.gitattributes` 会让脚本保持 Unix 换行，在 Windows 上 clone 也不会破坏 `deploy.sh`。

### 方式 B：直接在 Unraid 上 clone

先在 Unraid 终端里运行 `git --version` 确认有 git。没有的话，可以通过 NerdTools 等插件安装，或者改用方式 A。

```bash
cd /mnt/user/<共享>/apps
git clone https://github.com/sbihero/rawpick.git
```

仓库目前是公开的，不需要登录。如果以后改成私有，clone 时要用 GitHub 的
[fine-grained token](https://github.com/settings/personal-access-tokens) 当密码
（只授权 `rawpick` 仓库，权限 Contents: Read-only）。

## 第二步：配置

```bash
cd /mnt/user/<共享>/apps/rawpick
cp deploy.env.example deploy.env
nano deploy.env      # 也可以在 Windows 上用记事本编辑
```

至少要改 `PHOTO_ROOT`。`deploy.env` 不进 git，以后更新代码不会冲突。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PHOTO_ROOT` | `/mnt/user/photos` | 照片根目录，**必改** |
| `APPDATA` | `/mnt/user/appdata/rawpick` | 数据库和预览缓存，建议放在 SSD 缓存池上 |
| `PORT` | `8080` | 浏览器访问端口 |
| `PREVIEW_WORKERS` | `4` | 后台生成预览的线程数 |
| `PUID` / `PGID` | `99` / `100` | 容器以 `nobody:users` 身份运行，与 Unraid 共享的默认属主一致 |
| `UMASK` | `000` | 新建的 `select/` 和文件对所有人可读写，Windows 上能正常移动、删除 |
| `RAW_EXTS` | 空 | RAW 扩展名白名单，逗号分隔；空 = `arw,cr2,cr3,nef,raf,dng,orf,rw2` |
| `CONTAINER_NAME` / `IMAGE_NAME` | `rawpick` | 一般不用改 |

模板方式下，这些值只用于**第一次**生成模板时的默认值。之后的修改都在 Docker 页面的模板表单里完成。

## 第三步（模板方式）：构建镜像并安装模板

在 Unraid 网页右上角打开终端（`>_` 图标），运行：

```bash
cd /mnt/user/<共享>/apps/rawpick
bash deploy.sh --build-only
```

脚本会依次：

1. 检查照片根目录，创建 `APPDATA` 并把属主设为 99:100；
2. 构建本地镜像 `rawpick`，第一次大约一两分钟；
3. 把 `unraid/my-rawpick.xml` 安装到 `/boot/config/plugins/dockerMan/templates-user/`，
   并用 `deploy.env` 里的路径和端口填好默认值。模板已存在时不会覆盖。

然后在 Unraid 网页里：

1. 打开 **Docker** 页面，点最下面的 **Add Container**。
2. **Template** 下拉菜单里选 **rawpick**，表单会自动填好。
3. 检查**照片根目录**、**数据目录**、**WebUI 端口**。右上角切到 Advanced View 可以看到 Extra Parameters 里的 `--user 99:100`，不要删。
4. 点 **Apply**。

完成后，Docker 页面会出现 rawpick。点它的图标可以打开 WebUI、编辑、看日志，还能用右侧开关设置开机自动启动。

**更新：**

1. 拉取新代码：方式 A 在 GitHub Desktop 里点 Fetch / Pull；方式 B 在 Unraid 上运行 `git pull`。
2. 在 Unraid 终端里运行 `bash deploy.sh --build-only`。
3. Docker 页面 → rawpick → **Edit** → 不用改任何东西，直接点 **Apply**。容器会用新镜像重建。

Docker 页面的 “Check for Updates” 对本地构建的镜像不起作用（会显示 not available），这是正常的，按上面的步骤更新即可。

## 第三步（命令行方式）：一条命令部署

```bash
cd /mnt/user/<共享>/apps/rawpick
bash deploy.sh
```

脚本会构建镜像，停掉并删除旧容器，用 `docker run` 启动新容器（`--restart unless-stopped`，Unraid 重启后会自动启动），
等服务就绪后打印访问地址。

用这种方式启动的容器也会出现在 Docker 页面，可以启停、看日志，但**不能**在 Docker 页面的表单里改配置。
要改配置，就改 `deploy.env` 再运行一次 `bash deploy.sh`。

**更新**：拉取新代码，再运行一次 `bash deploy.sh`。

## 在两种方式之间切换

- **命令行 → 模板**：先运行 `docker rm -f rawpick` 删掉旧容器，再按模板方式操作。数据在 `APPDATA` 里，不会丢。
- **模板 → 命令行**：运行 `bash deploy.sh --force`。如果没加 `--force`，脚本发现容器是模板创建的会拒绝执行，
  免得误删。模板文件会保留，想用时可以再从 Add Container 里选。

## 访问

在电脑或 iPad 的浏览器里打开 `http://<Unraid IP>:8080`。
iPad 上可以用 Safari 的“添加到主屏幕”，像 App 一样全屏使用。

## 常用命令

```bash
docker logs -f rawpick       # 实时查看日志
docker restart rawpick       # 重启
bash deploy.sh --help        # 部署脚本帮助
```

卸载：在 Docker 页面删除容器（或 `docker rm -f rawpick`），再运行 `docker rmi rawpick`。
`APPDATA` 里的数据和模板文件 `/boot/config/plugins/dockerMan/templates-user/my-rawpick.xml` 需要另外手动删除。

## 数据与备份

`APPDATA` 目录下：

- `rawpick.db`：会话、标记、导出记录。只有这个文件值得备份。
- `cache/`：预览缓存，随时可以删掉，删了会自动重新生成。

照片目录里，rawpick 只会写 `<出门目录>/select/`。

## 排错

**脚本提示“照片根目录不存在”**
检查 `deploy.env` 里的 `PHOTO_ROOT`。可以在 Unraid 终端里用 `ls /mnt/user/` 看看有哪些共享。

**Add Container 的 Template 下拉菜单里没有 rawpick**
确认 `bash deploy.sh --build-only` 输出了“已安装 Unraid 模板”，然后刷新 Docker 页面。
也可以手动复制：`cp unraid/my-rawpick.xml /boot/config/plugins/dockerMan/templates-user/`。

**点 Apply 时提示拉取镜像失败**
模板里的 Repository 是本地镜像名 `rawpick`，Docker Hub 上没有它。先确认 `docker images rawpick` 能看到镜像
（也就是 `--build-only` 成功运行过），并且 Repository 字段和镜像名完全一致。

**容器启动后马上退出，日志里有 Permission denied**
数据目录的属主不对。运行 `chown 99:100 /mnt/user/appdata/rawpick`，或者重新运行 `bash deploy.sh --build-only`。

**首页提示没有找到文件夹**
出门目录必须**同时**有 `jpg/` 和 `raw/` 子目录（大小写不限），而且只认照片根目录下一层。

**预览生成很慢，或者刚打开时很慢**
第一次打开要读所有 JPG 并生成预览。阵列硬盘如果在休眠，要先等它转起来。生成期间可以照常筛选，当前那张会被优先生成。
CPU 核数多的话，可以调大预览线程数。

**Windows 上删不掉 `select/` 里的文件**
确认 UMASK 是 `000`，Extra Parameters 里有 `--user 99:100`。已经存在的文件可以在 Unraid 的 Tools → New Permissions 里修复。

**端口被占用**
模板方式在 Docker 页面 Edit 里改 WebUI 端口；命令行方式改 `deploy.env` 里的 `PORT`。

**iPad 打不开**
确认 iPad 和 Unraid 在同一个局域网，地址用 IP，不要用 `localhost`。

**`deploy.sh` 报 `$'\r': command not found`**
脚本被转换成了 Windows 换行。在 GitHub Desktop 里重新 clone；或者在 Unraid 上运行 `sed -i 's/\r$//' deploy.sh` 修复。

**服务启动失败**
运行 `docker logs rawpick` 看日志，把内容发给开发者。

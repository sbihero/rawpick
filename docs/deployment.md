# 部署指南（Unraid）

rawpick 以单个 Docker 容器的形式运行在 Unraid 上。部署脚本 `deploy.sh` 在 Unraid 本机构建镜像并启动容器，
不需要镜像仓库，Windows 上也不需要装 Python 或 Docker。

## 前提

- Unraid 已启用 Docker（Settings → Docker → Enable Docker: Yes）。
- Unraid 能访问外网：第一次构建要下载 Python 基础镜像和几个 Python 包。
- 照片按约定存放：照片根目录下每个出门目录里都有 `jpg/` 和 `raw/`。

## 第一步：把代码放到 Unraid 上

仓库是私有的，下面两种方式任选一种。

### 方式 A：在 Windows 上 clone 到共享里（推荐）

不需要在 Unraid 上装任何东西。

1. 在 Windows 上安装 [GitHub Desktop](https://desktop.github.com/) 并登录 GitHub。
2. File → Clone repository → 选 `sbihero/rawpick`，Local path 选一个映射到 Unraid 共享的目录，
   比如 `Z:\apps\rawpick`（`Z:` 映射的是 `\\unraid\<共享>`）。
3. 记下它在 Unraid 上对应的路径，比如 `/mnt/user/<共享>/apps/rawpick`。

仓库里的 `.gitattributes` 会让脚本保持 Unix 换行，在 Windows 上 clone 也不会破坏 `deploy.sh`。

### 方式 B：直接在 Unraid 上 clone

先在 Unraid 终端里运行 `git --version`，确认有 git；没有的话可以通过 NerdTools 等插件安装，或者改用方式 A。

1. 在 GitHub 上创建一个 [fine-grained token](https://github.com/settings/personal-access-tokens)：
   只授权 `rawpick` 仓库，权限 Contents: Read-only。
2. 在 Unraid 终端里运行：
   ```bash
   cd /mnt/user/<共享>/apps
   git clone https://github.com/sbihero/rawpick.git
   ```
   用户名填 GitHub 用户名，密码填刚才的 token。

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

## 第三步：部署

在 Unraid 网页右上角打开终端（`>_` 图标），运行：

```bash
cd /mnt/user/<共享>/apps/rawpick
bash deploy.sh
```

脚本会依次：

1. 检查照片根目录是否存在，创建 `APPDATA` 并设好属主；
2. 构建镜像 `rawpick`，第一次大约一两分钟；
3. 停掉并删除旧的 `rawpick` 容器（如果有）；
4. 启动新容器，设置 `--restart unless-stopped`，Unraid 重启后会自动启动；
5. 删除被替换下来的旧镜像；
6. 等待服务就绪，打印访问地址。

然后在电脑或 iPad 的浏览器里打开 `http://<Unraid IP>:8080`。iPad 上可以用 Safari 的“添加到主屏幕”，像 App 一样全屏使用。

用脚本启动的容器也会出现在 Unraid 的 Docker 页面，可以在那里启停、看日志。
不过它不是用 Unraid 模板创建的，所以**不能**在 Docker 页面的表单里改配置。要改配置，就改 `deploy.env` 再运行一次 `bash deploy.sh`。

## 更新

1. 拉取新代码：方式 A 在 GitHub Desktop 里点 Fetch / Pull；方式 B 在 Unraid 上运行 `git pull`。
2. 在 Unraid 终端里重新运行：
   ```bash
   bash deploy.sh
   ```

标记、导出记录和预览缓存都在 `APPDATA` 里，更新不会丢失。

## 常用命令

```bash
docker logs -f rawpick       # 实时查看日志
docker restart rawpick       # 重启
docker stop rawpick          # 停止
docker rm -f rawpick && docker rmi rawpick   # 卸载（APPDATA 里的数据需要另外手动删）
```

## 数据与备份

`APPDATA` 目录下：

- `rawpick.db`：会话、标记、导出记录。只有这个文件值得备份。
- `cache/`：预览缓存，随时可以删掉，删了会自动重新生成。

照片目录里，rawpick 只会写 `<出门目录>/select/`。

## 排错

**脚本提示“照片根目录不存在”**
检查 `deploy.env` 里的 `PHOTO_ROOT`。可以在 Unraid 终端里用 `ls /mnt/user/` 看看有哪些共享。

**首页提示没有找到文件夹**
照片根目录下的出门目录必须**同时**有 `jpg/` 和 `raw/` 子目录（大小写不限），而且只认根目录下一层。

**预览生成很慢，或者刚打开时很慢**
第一次打开要读所有 JPG 并生成预览。阵列硬盘如果在休眠，要先等它转起来。生成期间可以照常筛选，
当前那张会被优先生成。CPU 核数多的话可以调大 `PREVIEW_WORKERS`。

**Windows 上删不掉 `select/` 里的文件**
确认 `UMASK=000`、`PUID=99`、`PGID=100`。已经存在的文件可以在 Unraid 的 Tools → New Permissions 里修复。

**端口被占用**
把 `deploy.env` 里的 `PORT` 改成别的，比如 `8081`，再运行一次 `bash deploy.sh`。

**iPad 打不开**
确认 iPad 和 Unraid 在同一个局域网，地址用 IP，不要用 `localhost`。

**`deploy.sh` 报 `$'\r': command not found`**
脚本被转换成了 Windows 换行。在 GitHub Desktop 里重新 clone；或者在 Unraid 上运行 `sed -i 's/\r$//' deploy.sh` 修复。

**服务启动失败**
运行 `docker logs rawpick` 看日志，把内容发给开发者。

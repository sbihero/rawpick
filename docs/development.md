# 开发指南

## 项目结构

```
rawpick/                 Python 包（后端 + 编译好的前端）
  __main__.py            入口：python -m rawpick
  app.py                 HTTP 路由（Starlette）
  config.py              环境变量配置
  db.py                  SQLite 表结构和访问
  scanner.py             发现出门目录、配对、增量同步
  previews.py            预览生成、EXIF、后台队列
  exporter.py            导出清单与拷贝任务
  safety.py              照片目录里唯一的写入口
  static/                index.html、style.css、js/（tsc 编译输出，提交到仓库）
frontend/                前端 TypeScript 源码
  src/main.ts            路由
  src/home.ts            首页
  src/cull.ts            筛选界面：单张、网格、键盘、触屏、缩放
  src/preload.ts         预加载窗口
  src/exportPanel.ts     导出面板
  src/api.ts / util.ts   接口与工具
tests/                   pytest：单元测试、API 测试、tests/e2e 浏览器端到端测试
tools/make_testdata.py   生成测试用的出门目录
Dockerfile, deploy.sh    部署
docs/                    文档
```

## 本地运行

需要 Python 3.11 及以上。Windows、macOS、Linux 都可以。

```bash
pip install -r requirements-dev.txt

# 生成测试数据（也可以直接指向真实照片目录，比如 SMB 映射盘）
python tools/make_testdata.py ./photos --edge-cases

# 启动
PHOTO_ROOT=./photos DATA_DIR=./data python -m rawpick
# Windows PowerShell：
#   $env:PHOTO_ROOT="Z:\photos"; $env:DATA_DIR=".\data"; python -m rawpick
```

打开 <http://localhost:8080>。

环境变量见 `rawpick/config.py`：`PHOTO_ROOT`、`DATA_DIR`、`HOST`、`PORT`、`RAW_EXTS`、
`PREVIEW_WORKERS`、`THUMB_SIZE`、`PREVIEW_SIZE`、`UMASK`。

## 前端

源码在 `frontend/src`，用 TypeScript 编译器直接输出浏览器原生 ES 模块，不用打包器。

```bash
npm install -g typescript     # 一次性
tsc -p frontend               # 编译到 rawpick/static/js
tsc -p frontend --watch       # 开发时监听
```

**改完前端要把编译结果一起提交**：Docker 构建直接使用 `rawpick/static/js`，不会再编译。

TypeScript 里 import 要写 `.js` 后缀（如 `import { api } from "./api.js"`），这是浏览器原生模块的要求。

## 测试

```bash
python -m pytest -q                 # 全部
python -m pytest -q tests/e2e       # 只跑浏览器端到端测试
```

- 单元测试和 API 测试会自动生成小尺寸的测试数据，并在后台线程里启动真实的服务。
- 端到端测试用 Playwright 驱动 Chromium 内核浏览器：
  优先用系统 Chrome，可以用 `RAWPICK_CHROME=/path/to/chrome` 指定；都没有时用 `playwright install chromium` 装的浏览器。
- 安全相关的测试：
  - `test_safety.py`：路径穿越、符号链接、不覆盖；并扫描源码，确认 `safety.py` 以外没有写文件的调用。
  - `test_export.py` 和 `test_api.py`：对比导出前后照片目录里每个文件的大小、修改时间和内容。

## 测试数据

```bash
python tools/make_testdata.py <目录> [--sessions 2] [--count 30] [--width 1600] [--height 1067]
                                     [--raw-mb 2] [--edge-cases] [--noise]
```

- `--edge-cases`：在第一个出门目录里放进缺 RAW、只有 RAW、扩展名冲突、大小写混用、多余子目录、竖拍（Orientation=6）。
- `--noise`：生成带噪点的图，文件大小接近真实照片，测速度用，比如 `--width 6000 --height 4000 --noise`。

## 约定

- 照片目录里的任何写操作都必须通过 `safety.py`，`test_no_other_writes_into_photo_dirs` 会检查这一点。
- 阻塞操作（磁盘、数据库、图像处理）放在同步函数里，由 `app.py` 的 `api()` 包装进线程池执行。
- 数据库只有一个连接，所有访问都经过 `Database` 的锁。

"""启动入口：python -m rawpick

同一个进程里跑两个服务：
  * 管理员端（PORT，默认 8080）：粗筛、导出、发布相册。只在局域网里用，不要对外。
  * 客户端（CLIENT_PORT，默认 8081）：客户看相册、选片、下载成片。可以通过隧道对外。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal

import uvicorn

from .app import build_app
from .config import load_config


class _Server(uvicorn.Server):
    """信号由 main() 统一处理，收到退出信号时两个服务一起停。"""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


async def _serve(servers: list[uvicorn.Server]) -> None:
    def stop(*_):
        for s in servers:
            s.should_exit = True

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows 上没有 add_signal_handler
            loop.add_signal_handler(sig, stop)
    await asyncio.gather(*(s.serve() for s in servers))


def main() -> None:
    cfg = load_config()
    if cfg.umask is not None:
        os.umask(cfg.umask)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    app = build_app(cfg)
    common = dict(host=cfg.host, access_log=False, log_level="info")
    servers = [_Server(uvicorn.Config(app, port=cfg.port, **common))]
    if cfg.client_port:
        servers.append(_Server(uvicorn.Config(app.state.client_app, port=cfg.client_port,
                                              proxy_headers=False, **common)))
        logging.getLogger("rawpick").info("管理员端端口 %s，客户端端口 %s", cfg.port, cfg.client_port)
    asyncio.run(_serve(servers))


if __name__ == "__main__":
    main()

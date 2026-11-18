"""在后台线程里启动真实的 uvicorn 服务（管理员端 + 客户端两个端口），供 API 测试和端到端测试使用。"""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import uvicorn

from rawpick.app import build_app
from rawpick.config import Config


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    def __init__(self, cfg: Config):
        self.port = free_port()
        self.client_port = free_port()
        self.app = build_app(cfg)
        common = dict(host="127.0.0.1", log_level="warning", access_log=False)
        self.server = uvicorn.Server(uvicorn.Config(self.app, port=self.port, **common))
        self.client_server = uvicorn.Server(
            uvicorn.Config(self.app.state.client_app, port=self.client_port, **common))
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        async def both():
            await asyncio.gather(self.server.serve(), self.client_server.serve())

        asyncio.run(both())

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def client_url(self) -> str:
        return f"http://127.0.0.1:{self.client_port}"

    def __enter__(self) -> "LiveServer":
        self.thread.start()
        deadline = time.time() + 10
        while not (self.server.started and self.client_server.started):
            if time.time() > deadline:
                raise RuntimeError("服务没有按时启动")
            time.sleep(0.02)
        # 客户链接默认用“管理员端的主机名 + 客户端端口”，测试里客户端端口是随机的，写进设置
        import requests
        requests.put(f"{self.url}/api/settings", json={"client_base_url": self.client_url}, timeout=10)
        return self

    def __exit__(self, *exc) -> None:
        self.app.state.publisher.wait(10)
        self.server.should_exit = True
        self.client_server.should_exit = True
        self.thread.join(timeout=10)
        self.app.state.db.close()

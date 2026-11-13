"""在后台线程里启动一个真实的 uvicorn 服务，供 API 测试和端到端测试使用。"""

from __future__ import annotations

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
        self.app = build_app(cfg)
        self.server = uvicorn.Server(
            uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning",
                           access_log=False)
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> "LiveServer":
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("服务没有按时启动")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.app.state.db.close()

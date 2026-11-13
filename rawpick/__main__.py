"""启动入口：python -m rawpick"""

from __future__ import annotations

import logging
import os

import uvicorn

from .app import build_app
from .config import load_config


def main() -> None:
    cfg = load_config()
    if cfg.umask is not None:
        os.umask(cfg.umask)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    uvicorn.run(build_app(cfg), host=cfg.host, port=cfg.port, access_log=False, log_level="info")


if __name__ == "__main__":
    main()

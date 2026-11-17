"""HTTP 接口（Starlette）。阻塞操作统一放进线程池执行。"""

from __future__ import annotations

import functools
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import __version__
from .config import Config
from .db import Database
from .exporter import ExportBusy, ExportManager, build_plan
from .orphans import AdoptError, adopt, auto_adopt, blank_folders, find_orphans, manual_candidates
from .previews import KINDS, PreviewManager, read_exif_only
from .safety import UnsafePathError, session_dir
from .scanner import (
    count_jpgs, count_select_files, ensure_session_row, photo_version, survey_sessions,
    sync_session,
)

log = logging.getLogger("rawpick.app")

STATIC_DIR = Path(__file__).parent / "static"
IMMUTABLE = "public, max-age=31536000, immutable"


class ApiError(Exception):
    def __init__(self, status: int, message: str, **extra: Any):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


class RevalidatingStatic(StaticFiles):
    """前端静态文件每次都向服务器确认（有 ETag，没变化时只返回 304）。"""

    def file_response(self, *args, **kwargs) -> Response:
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


def photo_json(r: dict) -> dict:
    return {
        "id": r["id"],
        "name": r["jpg_name"],
        "raw": r["raw_name"],
        "picked": bool(r["picked"]),
        "v": photo_version(r["jpg_mtime_ns"], r["jpg_size"]),
        "exif": json.loads(r["exif"]) if r.get("exif") else None,
        "error": r.get("preview_error"),
    }


def build_app(cfg: Config) -> Starlette:
    db = Database(cfg.db_path)
    previews = PreviewManager(cfg, db)
    exports = ExportManager(cfg, db)

    # ------------------------------------------------------------ helpers
    def api(fn: Callable[..., Any]):
        @functools.wraps(fn)
        async def endpoint(request: Request) -> Response:
            body = None
            if request.method in ("POST", "PUT"):
                raw = await request.body()
                if raw:
                    try:
                        body = json.loads(raw)
                    except ValueError:
                        return JSONResponse({"error": "请求体不是合法 JSON"}, status_code=400)
            try:
                result = await run_in_threadpool(fn, request, body)
            except ApiError as e:
                return JSONResponse({"error": e.message, **e.extra}, status_code=e.status)
            except KeyError:
                return JSONResponse({"error": "不存在"}, status_code=404)
            except FileNotFoundError as e:
                return JSONResponse({"error": f"文件不存在：{e}"}, status_code=404)
            except UnsafePathError as e:
                return JSONResponse({"error": str(e)}, status_code=400)
            except Exception as e:  # pragma: no cover
                log.exception("请求出错 %s", request.url.path)
                return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
            if isinstance(result, Response):
                return result
            return JSONResponse(result)

        return endpoint

    def get_session(sid: int) -> dict:
        row = db.query_one("SELECT * FROM sessions WHERE id = ?", (sid,))
        if row is None:
            raise ApiError(404, "会话不存在")
        return row

    def session_json(row: dict, survey: dict | None = None) -> dict:
        """survey 是首页扫描得到的统计（JPG 张数、select/ 文件数），没有时当场计算。"""
        counts = db.query_one(
            "SELECT COUNT(*) AS total, COALESCE(SUM(picked), 0) AS picked FROM photos WHERE session_id = ?",
            (row["id"],),
        )
        scanned = row["last_scan_at"] is not None
        total = counts["total"]
        if survey is None:
            try:
                path = session_dir(cfg.photo_root, row["dir_name"])
                survey = {"jpg_count": count_jpgs(path) if not scanned else total,
                          "select_count": count_select_files(path)}
            except OSError:
                survey = {"jpg_count": 0, "select_count": 0}
        if not scanned:
            total = survey["jpg_count"]
        select_count = survey["select_count"]
        # select/ 里有文件就算已导出（包括不是用 rawpick 导出的）
        status = "exported" if select_count > 0 else ("opened" if scanned else "new")
        return {
            "id": row["id"],
            "name": row["dir_name"],
            "scanned": scanned,
            "total": total,
            "picked": counts["picked"],
            "select_count": select_count,
            "status": status,
            "last_export_at": row["last_export_at"],
            "cursor": row["cursor_photo_id"],
        }

    def photo_row(pid: int) -> dict:
        row = db.query_one(
            "SELECT p.*, s.dir_name FROM photos p JOIN sessions s ON s.id = p.session_id WHERE p.id = ?",
            (pid,),
        )
        if row is None:
            raise ApiError(404, "照片不存在")
        return row

    # ------------------------------------------------------------ 会话
    @api
    def list_sessions(request: Request, body: Any) -> dict:
        if not cfg.photo_root.is_dir():
            raise ApiError(500, f"照片根目录不存在：{cfg.photo_root}")
        survey = survey_sessions(cfg.photo_root, db)
        names = [i["name"] for i in survey]
        # 先尝试把改名前的记录自动接到新文件夹上（设置里可以关掉）
        if read_settings().get("auto_adopt", True):
            adopted, unmatched = auto_adopt(cfg, db, names)
        else:
            adopted, unmatched = [], len(find_orphans(db, names))
        out = []
        for info in survey:
            sid = ensure_session_row(db, info["name"])
            out.append(session_json(get_session(sid), info))
        return {"root": str(cfg.photo_root), "sessions": out, "adopted": adopted, "unmatched": unmatched}

    # ------------------------------------------------------------ 找回标记（手动模式）
    def folder_names() -> list[str]:
        if not cfg.photo_root.is_dir():
            raise ApiError(500, f"照片根目录不存在：{cfg.photo_root}")
        return [i["name"] for i in survey_sessions(cfg.photo_root, db)]

    @api
    def list_orphans(request: Request, body: Any) -> dict:
        return {"orphans": manual_candidates(cfg, db, folder_names())}

    @api
    def adopt_orphan(request: Request, body: Any) -> dict:
        sid = request.path_params["sid"]
        target = (body or {}).get("target")
        if not isinstance(target, str) or not target:
            raise ApiError(400, "需要 target：要关联到的文件夹名")
        names = folder_names()
        if sid not in {o["id"] for o in find_orphans(db, names)}:
            raise ApiError(400, "这条记录不是孤立记录（它的文件夹还在）")
        if target not in blank_folders(db, names):
            raise ApiError(400, "目标文件夹不存在，或者已经有标记了")
        try:
            adopt(db, sid, target)
        except AdoptError as e:
            raise ApiError(400, str(e))
        return {"ok": True, "session": session_json(get_session(sid))}

    @api
    def delete_orphan(request: Request, body: Any) -> dict:
        """删除一条孤立记录（文件夹已经不在了）：标记、导出记录、预览缓存。磁盘上的照片不受影响。"""
        sid = request.path_params["sid"]
        if sid not in {o["id"] for o in find_orphans(db, folder_names())}:
            raise ApiError(400, "这条记录不是孤立记录（它的文件夹还在），不能删除")
        db.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        previews.drop_session_cache(sid)
        return {"ok": True}

    @api
    def open_session(request: Request, body: Any) -> dict:
        sid = request.path_params["sid"]
        get_session(sid)
        summary = sync_session(cfg, db, sid)
        queued = previews.enqueue_session(sid)
        return {"session": session_json(get_session(sid)), "scan": summary, "queued": queued}

    @api
    def list_photos(request: Request, body: Any) -> dict:
        sid = request.path_params["sid"]
        sess = get_session(sid)
        rows = db.query(
            "SELECT * FROM photos WHERE session_id = ? ORDER BY jpg_name COLLATE NOCASE", (sid,)
        )
        return {"session": session_json(sess), "photos": [photo_json(r) for r in rows]}

    @api
    def list_issues(request: Request, body: Any) -> dict:
        sid = request.path_params["sid"]
        get_session(sid)
        rows = db.query("SELECT kind, name FROM issues WHERE session_id = ? ORDER BY kind, name", (sid,))
        return {"issues": rows}

    @api
    def session_status(request: Request, body: Any) -> dict:
        sid = request.path_params["sid"]
        get_session(sid)
        picked = db.query_one(
            "SELECT COALESCE(SUM(picked), 0) AS n FROM photos WHERE session_id = ?", (sid,)
        )["n"]
        return {"preview": previews.status(sid), "export_job": exports.running_job(sid), "picked": picked}

    @api
    def set_cursor(request: Request, body: Any) -> dict:
        sid = request.path_params["sid"]
        get_session(sid)
        pid = (body or {}).get("photo_id")
        if not isinstance(pid, int):
            raise ApiError(400, "需要 photo_id")
        db.execute("UPDATE sessions SET cursor_photo_id = ? WHERE id = ?", (pid, sid))
        return {"ok": True}

    # ------------------------------------------------------------ 照片
    @api
    def get_photo(request: Request, body: Any) -> dict:
        row = photo_row(request.path_params["pid"])
        if not row["exif"]:
            try:
                info = read_exif_only(previews.jpg_path(row, row["dir_name"]))
                row["exif"] = json.dumps(info, ensure_ascii=False)
                db.execute("UPDATE photos SET exif = ? WHERE id = ? AND exif IS NULL",
                           (row["exif"], row["id"]))
            except Exception:
                pass
        return photo_json(row)

    @api
    def set_picked(request: Request, body: Any) -> dict:
        row = photo_row(request.path_params["pid"])
        picked = (body or {}).get("picked")
        if not isinstance(picked, bool):
            raise ApiError(400, "需要 picked: true/false")
        db.execute("UPDATE photos SET picked = ? WHERE id = ?", (int(picked), row["id"]))
        n = db.query_one(
            "SELECT COALESCE(SUM(picked), 0) AS n FROM photos WHERE session_id = ?", (row["session_id"],)
        )["n"]
        return {"id": row["id"], "picked": picked, "picked_count": n}

    @api
    def image(request: Request, body: Any) -> Response:
        pid = request.path_params["pid"]
        kind = request.path_params["kind"]
        cache = IMMUTABLE if "v" in request.query_params else "no-cache"
        if kind == "original":
            row = photo_row(pid)
            path = previews.jpg_path(row, row["dir_name"])
            return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": cache})
        if kind not in KINDS:
            raise ApiError(404, "未知图片类型")
        try:
            row = previews.ensure(pid)
        except KeyError:
            raise ApiError(404, "照片不存在")
        except Exception as e:
            raise ApiError(500, f"预览生成失败：{e}")
        path = previews.cache_path(row["session_id"], pid, kind)
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": cache})

    # ------------------------------------------------------------ 导出
    @api
    def export_plan(request: Request, body: Any) -> dict:
        sid = request.path_params["sid"]
        get_session(sid)
        return build_plan(cfg, db, sid).to_dict()

    @api
    def start_export(request: Request, body: Any) -> dict:
        sid = request.path_params["sid"]
        get_session(sid)
        try:
            return {"job_id": exports.start(sid)}
        except ExportBusy as e:
            raise ApiError(409, "这个会话已有导出任务在运行", job_id=e.args[0])

    @api
    def get_job(request: Request, body: Any) -> dict:
        job = exports.get_job(request.path_params["jid"])
        if job is None:
            raise ApiError(404, "任务不存在")
        return job

    @api
    def health(request: Request, body: Any) -> dict:
        return {"ok": True, "version": __version__, "photo_root": str(cfg.photo_root),
                "photo_root_exists": cfg.photo_root.is_dir(), "data_dir": str(cfg.data_dir)}

    # ------------------------------------------------------------ 设置（存服务器，各设备共用）
    SETTING_KEYS = {"shortcuts", "auto_adopt"}

    def validate_setting(key: str, value: Any) -> None:
        if key == "auto_adopt" and not isinstance(value, bool):
            raise ApiError(400, "auto_adopt 需要 true 或 false")
        if key == "shortcuts":
            ok = isinstance(value, dict) and len(value) <= 50 and all(
                isinstance(k, str) and len(k) <= 40 and isinstance(v, list) and len(v) <= 10
                and all(isinstance(x, str) and 0 < len(x) <= 40 for x in v)
                for k, v in value.items()
            )
            if not ok:
                raise ApiError(400, "快捷键设置格式不对")

    def read_settings() -> dict:
        rows = db.query("SELECT key, value FROM settings")
        return {r["key"]: json.loads(r["value"]) for r in rows if r["key"] in SETTING_KEYS}

    @api
    def get_settings(request: Request, body: Any) -> dict:
        return read_settings()

    @api
    def put_settings(request: Request, body: Any) -> dict:
        """部分更新：只改请求里出现的项；值为 null 表示恢复默认。"""
        if not isinstance(body, dict):
            raise ApiError(400, "需要 JSON 对象")
        for key, value in body.items():
            if key not in SETTING_KEYS:
                raise ApiError(400, f"未知设置项：{key}")
            if value is not None:
                validate_setting(key, value)
        with db.transaction() as conn:
            for key, value in body.items():
                if value is None:
                    conn.execute("DELETE FROM settings WHERE key = ?", (key,))
                else:
                    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                                 (key, json.dumps(value, ensure_ascii=False)))
        return read_settings()

    async def index(request: Request) -> Response:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @asynccontextmanager
    async def lifespan(app: Starlette):
        exports.mark_interrupted_jobs()
        previews.start()
        log.info("rawpick %s 启动：照片根目录 %s，数据目录 %s", __version__, cfg.photo_root, cfg.data_dir)
        try:
            yield
        finally:
            previews.stop()

    routes = [
        Route("/", index),
        Route("/api/health", health),
        Route("/api/settings", get_settings, methods=["GET"]),
        Route("/api/settings", put_settings, methods=["PUT"]),
        Route("/api/sessions", list_sessions),
        Route("/api/orphans", list_orphans),
        Route("/api/orphans/{sid:int}/adopt", adopt_orphan, methods=["POST"]),
        Route("/api/orphans/{sid:int}", delete_orphan, methods=["DELETE"]),
        Route("/api/sessions/{sid:int}/open", open_session, methods=["POST"]),
        Route("/api/sessions/{sid:int}/photos", list_photos),
        Route("/api/sessions/{sid:int}/issues", list_issues),
        Route("/api/sessions/{sid:int}/status", session_status),
        Route("/api/sessions/{sid:int}/cursor", set_cursor, methods=["PUT"]),
        Route("/api/sessions/{sid:int}/export/plan", export_plan),
        Route("/api/sessions/{sid:int}/export", start_export, methods=["POST"]),
        Route("/api/photos/{pid:int}", get_photo),
        Route("/api/photos/{pid:int}/picked", set_picked, methods=["PUT"]),
        Route("/api/jobs/{jid:int}", get_job),
        Route("/img/{pid:int}/{kind:str}", image),
        Mount("/static", RevalidatingStatic(directory=STATIC_DIR), name="static"),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.db = db
    app.state.previews = previews
    app.state.exports = exports
    return app

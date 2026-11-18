"""客户端（相册）：单独一个端口，可以通过隧道对外。

这里只有相册相关的页面和接口，没有任何管理员接口；只读写数据库里的相册数据和 DATA_DIR/galleries/，
不接触照片目录（有测试检查）。客户通过 /g/<随机串> 访问，设了密码的相册要先输入密码。
"""

from __future__ import annotations

import functools
import hmac
import io
import json
import logging
import zipfile
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import quote

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import gallery_store as gs
from .db import Database

log = logging.getLogger("rawpick.client")

STATIC_DIR = Path(__file__).parent / "static"
PRIVATE_IMMUTABLE = "private, max-age=31536000, immutable"
SECURITY_HEADERS = {
    "X-Robots-Tag": "noindex, nofollow",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


class SecurityHeaders:
    """所有响应加上：不让搜索引擎收录、跳转时不带出链接、不允许被嵌进别的网页。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for k, v in SECURITY_HEADERS.items():
                    if k not in headers:
                        headers[k] = v
            await send(message)

        await self.app(scope, receive, send_with_headers)


class ClientStatic(StaticFiles):
    def file_response(self, *args, **kwargs) -> Response:
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


class _Sink(io.RawIOBase):
    """zipfile 写进来的字节先攒在这里，由生成器一段段吐给浏览器。"""

    def __init__(self) -> None:
        self.buf = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:  # type: ignore[override]
        self.buf += b
        return len(b)

    def take(self) -> bytes:
        out = bytes(self.buf)
        self.buf.clear()
        return out


def zip_stream(files: list[tuple[str, Path]]) -> Iterator[bytes]:
    """边打包边发送（不压缩，JPG 本来就压不动），不在磁盘上生成 zip。"""
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for arcname, path in files:
            info = zipfile.ZipInfo.from_file(path, arcname)
            info.compress_type = zipfile.ZIP_STORED
            with open(path, "rb") as src, zf.open(info, "w", force_zip64=True) as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
                    if len(sink.buf) >= 1024 * 1024:
                        yield sink.take()
            yield sink.take()
    yield sink.take()


def client_ip(request: Request) -> str:
    ip = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0]
    return ip.strip() or (request.client.host if request.client else "")


def build_client_app(galleries_dir: Path, db: Database) -> Starlette:
    limiter = gs.LoginLimiter()
    secret = gs.cookie_secret(db)

    def error(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
        return JSONResponse({"error": message, "code": code, **extra}, status_code=status)

    def gallery_or_raise(request: Request) -> dict:
        g = gs.get_by_token(db, request.path_params["token"])
        if g is None:
            raise gs.GalleryError(404, "not_found", "链接无效")
        if g["deleted_at"]:
            raise gs.GalleryError(410, "deleted", "相册已删除")
        return g

    def authed(request: Request, g: dict) -> bool:
        if not g["password_hash"]:
            return True
        got = request.cookies.get(gs.cookie_name(g), "")
        return hmac.compare_digest(got, gs.cookie_value(secret, g))

    def endpoint(auth: bool = True):
        """auth=True：需要已通过密码；处理函数在线程池里执行。"""

        def deco(fn: Callable[..., Any]):
            @functools.wraps(fn)
            async def handler(request: Request) -> Response:
                body = None
                if request.method in ("POST", "PUT"):
                    raw = await request.body()
                    if len(raw) > 16384:
                        return error(413, "too_large", "请求太大")
                    if raw:
                        try:
                            body = json.loads(raw)
                        except ValueError:
                            return error(400, "bad_request", "请求体不是合法 JSON")

                def run():
                    g = gallery_or_raise(request)
                    if auth and not authed(request, g):
                        raise gs.GalleryError(401, "auth_required", "需要密码")
                    return fn(request, g, body)

                try:
                    result = await run_in_threadpool(run)
                except gs.GalleryError as e:
                    return error(e.status, e.code, e.message, **e.extra)
                except Exception:  # pragma: no cover
                    log.exception("客户端请求出错 %s", request.url.path)
                    return error(500, "server_error", "服务器出错")
                if isinstance(result, Response):
                    return result
                return JSONResponse(result, headers={"Cache-Control": "no-store"})

            return handler

        return deco

    # ------------------------------------------------------------ 页面与信息
    async def page(request: Request) -> Response:
        return FileResponse(STATIC_DIR / "client.html", headers={"Cache-Control": "no-cache"})

    @endpoint(auth=False)
    def info(request: Request, g: dict, body: Any) -> dict:
        base = {"lang": g["lang"], "studio": g["watermark_text"]}
        if not authed(request, g):
            return {**base, "auth_required": True}
        c = gs.counts(db, g["id"])
        return {
            **base,
            "auth_required": False,
            "title": g["title"],
            "client_name": g["client_name"],
            "message": g["message"],
            "status": g["status"],
            "build": {"ready": c["ready"], "total": c["total"]} if g["status"] == "building" else None,
            "locked": gs.lock_reason(g),
            "deadline_at": g["deadline_at"],
            "delete_at": g["delete_at"],
            "limit": g["limit_count"],
            "limit_mode": g["limit_mode"],
            "total": c["ready"],
            "selected": c["selected"],
            "submitted_at": g["submitted_at"],
            "delivery": len(gs.deliveries(db, g["id"])) if gs.delivery_ready(g) else 0,
            "has_password": bool(g["password_hash"]),
        }

    @endpoint(auth=False)
    def login(request: Request, g: dict, body: Any) -> Response:
        ip = client_ip(request)
        if not g["password_hash"]:
            return JSONResponse({"ok": True})
        if limiter.blocked(g["id"], ip):
            raise gs.GalleryError(429, "too_many_attempts", "密码输错次数太多，请 15 分钟后再试")
        pw = (body or {}).get("password")
        if not isinstance(pw, str) or not gs.check_password(pw, g["password_hash"]):
            limiter.fail(g["id"], ip)
            raise gs.GalleryError(403, "bad_password", "密码不对")
        limiter.reset(g["id"], ip)
        resp = JSONResponse({"ok": True})
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        resp.set_cookie(gs.cookie_name(g), gs.cookie_value(secret, g), max_age=180 * 86400,
                        path=f"/g/{g['token']}", httponly=True, samesite="lax", secure=secure)
        return resp

    # ------------------------------------------------------------ 选片
    @endpoint()
    def photos(request: Request, g: dict, body: Any) -> dict:
        gs.ensure_open(g)
        return {"photos": gs.client_photos(db, g["id"])}

    @endpoint()
    def update(request: Request, g: dict, body: Any) -> dict:
        if not isinstance(body, dict):
            raise gs.GalleryError(400, "bad_request", "需要 JSON 对象")
        selected = body.get("selected")
        note = body.get("note")
        if selected is not None and not isinstance(selected, bool):
            raise gs.GalleryError(400, "bad_request", "selected 需要 true 或 false")
        if note is not None and not isinstance(note, str):
            raise gs.GalleryError(400, "bad_request", "note 需要字符串")
        return gs.update_photo(db, g, request.path_params["pid"], selected, note)

    @endpoint()
    def submit(request: Request, g: dict, body: Any) -> dict:
        return gs.submit(db, g, bool((body or {}).get("confirm")))

    @endpoint()
    def image(request: Request, g: dict, body: Any) -> Response:
        gs.ensure_open(g)
        kind = request.path_params["kind"]
        pid = request.path_params["pid"]
        if kind not in gs.PHOTO_KINDS:
            raise gs.GalleryError(404, "not_found", "不存在")
        row = db.query_one("SELECT id FROM gallery_photos WHERE id = ? AND gallery_id = ? AND ready = 1",
                           (pid, g["id"]))
        path = gs.photo_file(galleries_dir, g["id"], pid, kind)
        if row is None or not path.is_file():
            raise gs.GalleryError(404, "not_found", "照片不存在")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": PRIVATE_IMMUTABLE})

    # ------------------------------------------------------------ 成片
    def delivery_rows(g: dict) -> list[dict]:
        if not gs.delivery_ready(g):
            raise gs.GalleryError(404, "no_delivery", "还没有成片")
        return gs.deliveries(db, g["id"])

    @endpoint()
    def delivery(request: Request, g: dict, body: Any) -> dict:
        return {"photos": delivery_rows(g)}

    @endpoint()
    def delivery_image(request: Request, g: dict, body: Any) -> Response:
        rows = {r["id"]: r for r in delivery_rows(g)}
        did = request.path_params["did"]
        kind = request.path_params["kind"]
        if did not in rows or kind not in gs.DELIVERY_KINDS:
            raise gs.GalleryError(404, "not_found", "不存在")
        path = gs.delivery_file(galleries_dir, g["id"], did, kind)
        if not path.is_file():
            raise gs.GalleryError(404, "not_found", "文件不存在")
        if kind == "file":
            return FileResponse(path, media_type="image/jpeg", filename=rows[did]["name"],
                                headers={"Cache-Control": "private, no-cache"})
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": PRIVATE_IMMUTABLE})

    @endpoint()
    def delivery_zip(request: Request, g: dict, body: Any) -> Response:
        rows = delivery_rows(g)
        files = []
        seen: set[str] = set()
        for r in rows:
            name = r["name"]
            if name.lower() in seen:  # 理论上不会重名，防万一
                stem, dot, ext = name.rpartition(".")
                name = f"{stem}_{r['id']}{dot}{ext}"
            seen.add(name.lower())
            files.append((name, gs.delivery_file(galleries_dir, g["id"], r["id"], "file")))
        safe_title = "".join(c for c in g["title"] if c not in '\\/:*?"<>|').strip() or "photos"
        headers = {
            "Content-Disposition": f"attachment; filename=\"photos.zip\"; filename*=UTF-8''{quote(safe_title)}.zip",
            "Cache-Control": "private, no-cache",
        }
        return StreamingResponse(zip_stream(files), media_type="application/zip", headers=headers)

    async def not_found(request: Request) -> Response:
        return PlainTextResponse("Not Found", status_code=404)

    routes = [
        Route("/g/{token:str}", page),
        Route("/g/{token:str}/", page),
        Route("/g/{token:str}/api/info", info),
        Route("/g/{token:str}/api/login", login, methods=["POST"]),
        Route("/g/{token:str}/api/photos", photos),
        Route("/g/{token:str}/api/photos/{pid:int}", update, methods=["PUT"]),
        Route("/g/{token:str}/api/submit", submit, methods=["POST"]),
        Route("/g/{token:str}/img/{pid:int}/{kind:str}", image),
        Route("/g/{token:str}/api/delivery", delivery),
        Route("/g/{token:str}/d/all.zip", delivery_zip),
        Route("/g/{token:str}/d/{did:int}/{kind:str}", delivery_image),
        Mount("/static", ClientStatic(directory=STATIC_DIR), name="static"),
        Route("/", not_found),
    ]
    return Starlette(routes=routes, middleware=[Middleware(SecurityHeaders)])

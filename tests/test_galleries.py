"""客户相册：发布、客户端隔离、密码、张数上限、截止、导出客户所选 RAW、交付成片、自动删除。"""

from __future__ import annotations

import io
import shutil
import sqlite3
import time
import zipfile

import pytest
import requests
from conftest import SESSION1, SESSION2, snapshot
from PIL import Image
from server_util import LiveServer

from rawpick.db import Database


@pytest.fixture
def srv(cfg):
    with LiveServer(cfg) as s:
        yield s


def A(srv, method, path, **kw):
    return requests.request(method, srv.url + path, timeout=20, **kw)


def session_id(srv, name=SESSION1) -> int:
    for s in A(srv, "GET", "/api/sessions").json()["sessions"]:
        if s["name"] == name:
            return s["id"]
    raise KeyError(name)


def pick_first(srv, sid, n) -> list[dict]:
    A(srv, "POST", f"/api/sessions/{sid}/open")
    photos = A(srv, "GET", f"/api/sessions/{sid}/photos").json()["photos"]
    for p in photos[:n]:
        A(srv, "PUT", f"/api/photos/{p['id']}/picked", json={"picked": True})
    return photos[:n]


def publish(srv, n=4, name=SESSION1, **opts) -> dict:
    sid = session_id(srv, name)
    pick_first(srv, sid, n)
    body = {"title": "测试相册", **opts}
    r = A(srv, "POST", f"/api/sessions/{sid}/galleries", json=body)
    assert r.status_code == 200, r.text
    srv.app.state.publisher.wait()
    g = A(srv, "GET", f"/api/galleries/{r.json()['id']}").json()
    assert g["status"] == "open", g
    return g


def client(g, password=None) -> requests.Session:
    c = requests.Session()
    c.base = g["link"]  # type: ignore[attr-defined]
    if password:
        assert c.post(g["link"] + "/api/login", json={"password": password}, timeout=10).status_code == 200
    return c


def C(c, method, path, **kw):
    return c.request(method, c.base + path, timeout=20, **kw)


# ---------------------------------------------------------------- 发布与隔离

def test_publish_requires_picked(srv):
    sid = session_id(srv)
    r = A(srv, "POST", f"/api/sessions/{sid}/galleries", json={"title": "x"})
    assert r.status_code == 400 and "已选" in r.json()["error"]
    assert A(srv, "POST", f"/api/sessions/{sid}/galleries", json={}).status_code == 400
    assert A(srv, "POST", f"/api/sessions/{sid}/galleries",
             json={"title": "x", "limit_count": 0}).status_code == 400


def test_publish_builds_bundle_and_never_writes_photo_dir(srv, cfg, photo_root):
    before = snapshot(photo_root)
    g = publish(srv, n=4, client_name="王先生", limit_count=3, lang="en")
    assert g["counts"] == {"total": 4, "ready": 4, "failed": 0, "selected": 0, "notes": 0}
    assert g["link"].startswith(srv.client_url + "/g/") and len(g["link"].rsplit("/", 1)[1]) >= 22
    assert len(g["code"]) == 6
    assert g["export_dir"].startswith("client_") and g["export_dir"].endswith("_" + g["code"])
    bundle = cfg.galleries_dir / str(g["id"]) / "p"
    files = sorted(p.name for p in bundle.iterdir())
    assert len(files) == 8
    with Image.open(bundle / files[1]) as im:
        assert max(im.size) <= cfg.gallery_preview_size
    assert snapshot(photo_root) == before
    listed = A(srv, "GET", "/api/galleries").json()["galleries"]
    assert [x["id"] for x in listed] == [g["id"]]


def test_watermark_changes_pixels(srv, cfg):
    g1 = publish(srv, n=1, watermark=True)
    g2 = publish(srv, n=1, watermark=False)
    p1 = cfg.galleries_dir / str(g1["id"]) / "p" / f"{g1['photos'][0]['id']}-preview.jpg"
    p2 = cfg.galleries_dir / str(g2["id"]) / "p" / f"{g2['photos'][0]['id']}-preview.jpg"
    with Image.open(p1) as a, Image.open(p2) as b:
        assert a.size == b.size
        w, h = a.size
        box = (w // 3, h // 3, 2 * w // 3, 2 * h // 3)  # 水印在中间
        diff = sum(abs(x - y) for x, y in zip(a.crop(box).tobytes(), b.crop(box).tobytes()))
        corner = sum(abs(x - y) for x, y in zip(a.crop((0, 0, 20, 20)).tobytes(), b.crop((0, 0, 20, 20)).tobytes()))
    assert diff > 50 * corner + 1000
    assert g1["watermark_text"] == "严记肘子铺"


def test_client_port_has_no_admin_routes(srv):
    g = publish(srv, n=1)
    for path in ("/", "/api/health", "/api/sessions", "/api/settings", "/api/galleries",
                 f"/api/galleries/{g['id']}", "/img/1/thumb", "/img/1/original"):
        r = requests.get(srv.client_url + path, timeout=10)
        assert r.status_code == 404, path
    r = requests.put(srv.client_url + "/api/photos/1/picked", json={"picked": True}, timeout=10)
    assert r.status_code in (404, 405)
    # 管理员端也没有客户端的路由
    token = g["link"].rsplit("/", 1)[1]
    assert A(srv, "GET", f"/g/{token}/api/info").status_code == 404


def test_client_security_headers_and_page(srv):
    g = publish(srv, n=1)
    r = requests.get(g["link"], timeout=10)
    assert r.status_code == 200 and "client-main.js" in r.text
    assert r.headers["x-robots-tag"] == "noindex, nofollow"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-frame-options"] == "DENY"
    assert requests.get(srv.client_url + "/g/nope/api/info", timeout=10).json()["code"] == "not_found"


# ---------------------------------------------------------------- 密码

def test_password_flow(srv):
    g = publish(srv, n=2, password="secret1")
    c = client(g)
    info = C(c, "GET", "/api/info").json()
    assert info == {"lang": "auto", "studio": "严记肘子铺", "auth_required": True}
    assert C(c, "GET", "/api/photos").status_code == 401
    pid = g["photos"][0]["id"]
    assert C(c, "GET", f"/img/{pid}/thumb").status_code == 401
    assert C(c, "POST", "/api/login", json={"password": "wrong"}).json()["code"] == "bad_password"
    assert C(c, "POST", "/api/login", json={"password": "secret1"}).status_code == 200
    assert C(c, "GET", "/api/info").json()["title"] == "测试相册"
    assert C(c, "GET", f"/img/{pid}/thumb").headers["content-type"] == "image/jpeg"
    # 改密码后旧的登录失效
    A(srv, "PUT", f"/api/galleries/{g['id']}", json={"password": "secret2"})
    assert C(c, "GET", "/api/photos").status_code == 401
    # 改成公开后不需要密码
    A(srv, "PUT", f"/api/galleries/{g['id']}", json={"password": None})
    assert requests.get(g["link"] + "/api/photos", timeout=10).status_code == 200


def test_password_attempts_limited(srv):
    g = publish(srv, n=1, password="right")
    c = client(g)
    for _ in range(10):
        assert C(c, "POST", "/api/login", json={"password": "x"}).json()["code"] == "bad_password"
    r = C(c, "POST", "/api/login", json={"password": "right"})
    assert r.status_code == 429 and r.json()["code"] == "too_many_attempts"
    # 其他来源不受影响
    other = C(c, "POST", "/api/login", json={"password": "right"}, headers={"CF-Connecting-IP": "203.0.113.9"})
    assert other.status_code == 200


def test_reset_link(srv):
    g = publish(srv, n=1)
    old = g["link"]
    g2 = A(srv, "POST", f"/api/galleries/{g['id']}/reset-link").json()
    assert g2["link"] != old
    assert requests.get(old + "/api/info", timeout=10).status_code == 404
    assert requests.get(g2["link"] + "/api/info", timeout=10).status_code == 200


# ---------------------------------------------------------------- 选片、上限、截止

def test_select_note_and_submit_block(srv):
    g = publish(srv, n=4, limit_count=2, limit_mode="block")
    c = client(g)
    photos = C(c, "GET", "/api/photos").json()["photos"]
    assert [p["selected"] for p in photos] == [False] * 4
    assert C(c, "POST", "/api/submit", json={}).json()["code"] == "nothing_selected"
    for p in photos[:3]:  # 选的时候不拦
        r = C(c, "PUT", f"/api/photos/{p['id']}", json={"selected": True})
        assert r.status_code == 200
    assert r.json()["selected_count"] == 3
    assert C(c, "PUT", f"/api/photos/{photos[0]['id']}", json={"note": "  去掉路人  "}).json()["note"] == "去掉路人"
    assert C(c, "PUT", f"/api/photos/{photos[0]['id']}", json={"note": "x" * 1001}).status_code == 400
    assert C(c, "PUT", f"/api/photos/{photos[0]['id']}", json={"selected": "yes"}).status_code == 400
    assert C(c, "PUT", "/api/photos/999999", json={"selected": True}).status_code == 404
    # 超出上限、不能提交
    r = C(c, "POST", "/api/submit", json={"confirm": True})
    assert r.status_code == 400 and r.json()["code"] == "over_limit"
    C(c, "PUT", f"/api/photos/{photos[2]['id']}", json={"selected": False})
    r = C(c, "POST", "/api/submit", json={})
    assert r.status_code == 200 and r.json()["selected"] == 2
    info = C(c, "GET", "/api/info").json()
    assert info["locked"] == "submitted" and info["status"] == "submitted"
    r = C(c, "PUT", f"/api/photos/{photos[3]['id']}", json={"selected": True})
    assert r.status_code == 409 and r.json()["code"] == "locked"
    # 管理员端看到选择和备注；重新开放后客户可以继续改
    d = A(srv, "GET", f"/api/galleries/{g['id']}").json()
    assert d["counts"]["selected"] == 2 and d["counts"]["notes"] == 1 and d["status"] == "submitted"
    assert d["photos"][0]["note"] == "去掉路人"
    assert A(srv, "POST", f"/api/galleries/{g['id']}/reopen").json()["status"] == "open"
    assert C(c, "PUT", f"/api/photos/{photos[3]['id']}", json={"selected": True}).status_code == 200
    assert A(srv, "POST", f"/api/galleries/{g['id']}/reopen").status_code == 400


def test_submit_warn_mode_needs_confirm(srv):
    g = publish(srv, n=3, limit_count=1, limit_mode="warn")
    c = client(g)
    for p in C(c, "GET", "/api/photos").json()["photos"]:
        C(c, "PUT", f"/api/photos/{p['id']}", json={"selected": True})
    r = C(c, "POST", "/api/submit", json={})
    assert r.status_code == 409 and r.json()["code"] == "over_limit_confirm" and r.json()["selected"] == 3
    assert C(c, "POST", "/api/submit", json={"confirm": True}).status_code == 200


def test_deadline_locks_changes(srv):
    g = publish(srv, n=2, deadline_at=time.time() + 3600)
    c = client(g)
    pid = g["photos"][0]["id"]
    assert C(c, "PUT", f"/api/photos/{pid}", json={"selected": True}).status_code == 200
    A(srv, "PUT", f"/api/galleries/{g['id']}", json={"deadline_at": time.time() - 1})
    info = C(c, "GET", "/api/info").json()
    assert info["locked"] == "deadline"
    assert C(c, "PUT", f"/api/photos/{pid}", json={"note": "x"}).json()["code"] == "locked"
    assert C(c, "POST", "/api/submit", json={}).json()["code"] == "locked"
    # 截止后照片还能看
    assert C(c, "GET", "/api/photos").status_code == 200
    # 延长截止日期后又能改
    A(srv, "PUT", f"/api/galleries/{g['id']}", json={"deadline_at": None})
    assert C(c, "PUT", f"/api/photos/{pid}", json={"selected": False}).status_code == 200


def test_gallery_option_validation(srv):
    g = publish(srv, n=1)
    put = lambda body: A(srv, "PUT", f"/api/galleries/{g['id']}", json=body)
    assert put({"lang": "fr"}).status_code == 400
    assert put({"limit_mode": "x"}).status_code == 400
    assert put({"limit_count": True}).status_code == 400
    assert put({"deadline_at": "tomorrow"}).status_code == 400
    assert put({"password": ""}).status_code == 400
    assert put({"title": ""}).status_code == 400
    r = put({"title": "新标题", "limit_count": 10, "limit_mode": "warn", "lang": "zh", "message": "你好"})
    assert r.status_code == 200
    j = r.json()
    assert (j["title"], j["limit"], j["limit_mode"], j["lang"], j["message"]) == ("新标题", 10, "warn", "zh", "你好")
    # 水印发布后不能改（PUT 里的 watermark 被忽略）
    assert put({"watermark": False}).json()["watermark"] is True


# ---------------------------------------------------------------- 导出客户所选 RAW

def test_export_client_selection(srv, photo_root):
    g = publish(srv, n=4)
    c = client(g)
    photos = C(c, "GET", "/api/photos").json()["photos"]
    for p in photos[:2]:
        C(c, "PUT", f"/api/photos/{p['id']}", json={"selected": True})
    before = snapshot(photo_root)
    plan = A(srv, "GET", f"/api/galleries/{g['id']}/export/plan").json()
    assert plan["dest"] == g["export_dir"] and plan["picked"] == 2 and len(plan["copy"]) == 2
    job = A(srv, "POST", f"/api/galleries/{g['id']}/export", json={}).json()["job_id"]
    for _ in range(200):
        j = A(srv, "GET", f"/api/jobs/{job}").json()
        if j["status"] != "running":
            break
        time.sleep(0.05)
    assert j["status"] == "done" and len(j["report"]["copied"]) == 2 and j["dest"] == g["export_dir"]
    out = photo_root / SESSION1 / g["export_dir"]
    assert sorted(p.name for p in out.iterdir()) == sorted(plan["copy"])
    # 源文件没变，select/ 没有被创建
    after = snapshot(photo_root)
    assert {k: v for k, v in after.items() if g["export_dir"] not in k} == before
    assert not (photo_root / SESSION1 / "select").exists()
    # 再导出一次全部跳过；session 的 last_export_at 不受影响
    plan2 = A(srv, "GET", f"/api/galleries/{g['id']}/export/plan").json()
    assert plan2["copy"] == [] and len(plan2["skip"]) == 2
    d = A(srv, "GET", f"/api/galleries/{g['id']}").json()
    assert d["last_export_at"] is not None
    sess = [s for s in A(srv, "GET", "/api/sessions").json()["sessions"] if s["name"] == SESSION1][0]
    assert sess["last_export_at"] is None and sess["status"] == "opened"
    # 自定义目录名；不能导出到 jpg/raw 或带路径的名字
    assert A(srv, "GET", f"/api/galleries/{g['id']}/export/plan?dest=王先生精修").json()["dest"] == "王先生精修"
    for bad in ("jpg", "RAW", "../x", "a/b", ".x"):
        assert A(srv, "GET", f"/api/galleries/{g['id']}/export/plan", params={"dest": bad}).status_code == 400
        assert A(srv, "POST", f"/api/galleries/{g['id']}/export", json={"dest": bad}).status_code == 400


# ---------------------------------------------------------------- 交付成片

def test_delivery(srv, photo_root):
    g = publish(srv, n=2)
    sess = photo_root / SESSION1
    (sess / "select").mkdir()
    (sess / "Final").mkdir()
    (sess / "empty").mkdir()
    (sess / ".hidden").mkdir()
    srcs = sorted((sess / "jpg").glob("*.JPG"))[:3]
    for f in srcs:
        shutil.copy2(f, sess / "Final" / f.name)
    (sess / "Final" / "notes.txt").write_text("x")
    folders = A(srv, "GET", f"/api/galleries/{g['id']}/delivery/folders").json()["folders"]
    assert folders == [{"name": "empty", "jpg_count": 0}, {"name": "Final", "jpg_count": 3}]
    c = client(g)
    assert C(c, "GET", "/api/delivery").json()["code"] == "no_delivery"
    assert A(srv, "POST", f"/api/galleries/{g['id']}/delivery", json={"folder": "jpg"}).status_code == 400
    assert A(srv, "POST", f"/api/galleries/{g['id']}/delivery", json={"folder": "../x"}).status_code == 400
    before = snapshot(photo_root)
    A(srv, "POST", f"/api/galleries/{g['id']}/delivery", json={"folder": "Final"})
    srv.app.state.publisher.wait()
    assert snapshot(photo_root) == before
    d = A(srv, "GET", f"/api/galleries/{g['id']}").json()
    assert d["delivery_status"] == "ready" and d["delivery_dir"] == "Final"
    assert C(c, "GET", "/api/info").json()["delivery"] == 3
    items = C(c, "GET", "/api/delivery").json()["photos"]
    assert [i["name"] for i in items] == [f.name for f in srcs]
    r = C(c, "GET", f"/d/{items[0]['id']}/file")
    assert r.content == srcs[0].read_bytes()
    assert srcs[0].name in r.headers["content-disposition"]
    assert C(c, "GET", f"/d/{items[0]['id']}/thumb").headers["content-type"] == "image/jpeg"
    z = C(c, "GET", "/d/all.zip")
    assert z.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(z.content))
    assert zf.namelist() == [f.name for f in srcs]
    assert zf.read(srcs[1].name) == srcs[1].read_bytes()
    # 撤回
    A(srv, "DELETE", f"/api/galleries/{g['id']}/delivery")
    assert C(c, "GET", "/api/delivery").json()["code"] == "no_delivery"
    assert C(c, "GET", f"/d/{items[0]['id']}/file").status_code == 404


# ---------------------------------------------------------------- 删除

def test_auto_delete_keeps_selection(srv, cfg):
    g = publish(srv, n=2, delete_at=time.time() + 3600)
    c = client(g)
    pid = C(c, "GET", "/api/photos").json()["photos"][0]["id"]
    C(c, "PUT", f"/api/photos/{pid}", json={"selected": True, "note": "留着"})
    pub = srv.app.state.publisher
    assert pub.sweep() == []
    assert pub.sweep(now=time.time() + 7200) == [g["id"]]
    assert not (cfg.galleries_dir / str(g["id"])).exists()
    r = C(c, "GET", "/api/info")
    assert r.status_code == 410 and r.json()["code"] == "deleted"
    d = A(srv, "GET", f"/api/galleries/{g['id']}").json()
    assert d["deleted_at"] and d["counts"]["selected"] == 1 and d["photos"][0]["note"] == "留着"
    # 选择还在，RAW 照样能导出
    assert A(srv, "GET", f"/api/galleries/{g['id']}/export/plan").json()["picked"] == 1
    assert A(srv, "POST", f"/api/galleries/{g['id']}/delivery", json={"folder": "x"}).status_code == 400


def test_manual_delete(srv, cfg, photo_root):
    g = publish(srv, n=1)
    before = snapshot(photo_root)
    assert A(srv, "DELETE", f"/api/galleries/{g['id']}").json() == {"ok": True}
    assert not (cfg.galleries_dir / str(g["id"])).exists()
    assert A(srv, "GET", f"/api/galleries/{g['id']}").status_code == 404
    assert requests.get(g["link"] + "/api/info", timeout=10).status_code == 404
    assert snapshot(photo_root) == before


def test_building_state_and_resume(cfg):
    """生成到一半服务重启：重新启动后接着生成。"""
    with LiveServer(cfg) as s:
        sid = session_id(s, SESSION2)
        pick_first(s, sid, 2)
        pub = s.app.state.publisher
        pub._spawn = lambda *a, **k: None  # 不启动后台生成，模拟重启前没做完
        g = A(s, "POST", f"/api/sessions/{sid}/galleries", json={"title": "x"}).json()
        assert g["status"] == "building"
        c = client(g)
        info = C(c, "GET", "/api/info").json()
        assert info["status"] == "building" and info["build"] == {"ready": 0, "total": 2}
        assert C(c, "GET", "/api/photos").json()["code"] == "building"
    with LiveServer(cfg) as s:
        s.app.state.publisher.wait()
        assert A(s, "GET", f"/api/galleries/{g['id']}").json()["status"] == "open"


def test_db_migration_from_v02(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE sessions (id INTEGER PRIMARY KEY, dir_name TEXT NOT NULL UNIQUE, last_scan_at REAL,
            last_export_at REAL, cursor_photo_id INTEGER);
        CREATE TABLE export_jobs (id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL, status TEXT NOT NULL,
            total INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0, bytes_total INTEGER NOT NULL DEFAULT 0,
            bytes_done INTEGER NOT NULL DEFAULT 0, report TEXT, error TEXT, started_at REAL, finished_at REAL);
        INSERT INTO sessions (id, dir_name) VALUES (1, 'a');
        INSERT INTO export_jobs (session_id, status) VALUES (1, 'done');
    """)
    conn.commit()
    conn.close()
    db = Database(path)
    row = db.query_one("SELECT * FROM export_jobs")
    assert row["dest"] == "select" and row["gallery_id"] is None
    assert db.query("SELECT * FROM galleries") == []
    db.close()


def test_auto_link_and_port_warning(srv, cfg):
    g = publish(srv, n=1)
    assert g["link_uncertain"] is False  # 测试服务设置了客户访问地址
    A(srv, "PUT", "/api/settings", json={"client_base_url": None})
    d = A(srv, "GET", f"/api/galleries/{g['id']}").json()
    # 没设置时用“管理员端主机名 + 容器里的客户端端口”；测试服务的端口和配置里的不同，所以提示可能不对
    assert d["link"].startswith(f"http://127.0.0.1:{cfg.client_port}/g/")
    assert d["link_uncertain"] is True


def test_delivery_follows_renamed_folder(srv, photo_root):
    """发布后出门文件夹改名（找回标记自动关联）：交付和详情页跟着用新名字。"""
    g = publish(srv, n=2, name=SESSION2)
    new = "20261107_改名后"
    (photo_root / SESSION2).rename(photo_root / new)
    A(srv, "GET", "/api/sessions")  # 首页刷新时自动关联
    (photo_root / new / "final").mkdir()
    shutil.copy2(sorted((photo_root / new / "jpg").glob("*.JPG"))[0], photo_root / new / "final")
    d = A(srv, "GET", f"/api/galleries/{g['id']}").json()
    assert d["dir_name"] == new
    assert A(srv, "GET", f"/api/galleries/{g['id']}/delivery/folders").json()["folders"] == \
        [{"name": "final", "jpg_count": 1}]
    A(srv, "POST", f"/api/galleries/{g['id']}/delivery", json={"folder": "final"})
    srv.app.state.publisher.wait()
    assert A(srv, "GET", f"/api/galleries/{g['id']}").json()["delivery_status"] == "ready"

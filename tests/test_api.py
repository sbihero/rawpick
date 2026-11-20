from __future__ import annotations

import time

import pytest
import requests
from conftest import SESSION1, SESSION2, snapshot
from server_util import LiveServer


@pytest.fixture
def srv(cfg):
    with LiveServer(cfg) as s:
        yield s


def get(srv, path, **kw):
    r = requests.get(srv.url + path, timeout=10, **kw)
    return r


def test_health_and_index(srv):
    assert get(srv, "/api/health").json()["ok"] is True
    r = get(srv, "/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]


def test_sessions_listing(srv):
    data = get(srv, "/api/sessions").json()
    names = [s["name"] for s in data["sessions"]]
    assert names == [SESSION1, SESSION2]
    s1 = data["sessions"][0]
    assert s1["scanned"] is False and s1["total"] == 13  # 未扫描时按 jpg 文件数估算
    assert s1["picked"] == 0 and s1["status"] == "new" and s1["select_count"] == 0
    assert data["adopted"] == [] and data["unmatched"] == 0


def test_session_status(srv, photo_root):
    sid = get(srv, "/api/sessions").json()["sessions"][0]["id"]
    requests.post(f"{srv.url}/api/sessions/{sid}/open", timeout=10)
    assert get(srv, "/api/sessions").json()["sessions"][0]["status"] == "opened"
    # select/ 里有文件就算已导出，不管是不是 rawpick 导出的
    sel = photo_root / SESSION2 / "select"
    sel.mkdir()
    (sel / "x.ARW").write_bytes(b"1")
    s2 = get(srv, "/api/sessions").json()["sessions"][1]
    assert s2["status"] == "exported" and s2["select_count"] == 1 and s2["scanned"] is False


def test_settings(srv):
    put = lambda body: requests.put(f"{srv.url}/api/settings", json=body, timeout=10)
    # 测试服务启动时写入了 client_base_url，先清掉
    assert put({"client_base_url": None}).json() == {}
    assert get(srv, "/api/settings").json() == {}
    r = put({"shortcuts": {"next": ["KeyJ", "ArrowRight"]}, "auto_adopt": False})
    assert r.json() == {"shortcuts": {"next": ["KeyJ", "ArrowRight"]}, "auto_adopt": False}
    assert get(srv, "/api/settings").json()["auto_adopt"] is False
    assert put({"shortcuts": None}).json() == {"auto_adopt": False}
    assert put({"nope": 1}).status_code == 400
    assert put({"shortcuts": {"next": "KeyJ"}}).status_code == 400
    assert put({"auto_adopt": "yes"}).status_code == 400
    assert put([1]).status_code == 400
    # 客户相册相关
    assert put({"watermark_text": "严记肘子铺"}).json()["watermark_text"] == "严记肘子铺"
    assert put({"watermark_text": " "}).status_code == 400
    assert put({"watermark_text": "x" * 41}).status_code == 400
    assert put({"client_base_url": "https://photos.example.com"}).status_code == 200
    assert put({"client_base_url": "ftp://x"}).status_code == 400
    # 内部的 Cookie 密钥不能通过接口读写
    assert "_cookie_secret" not in get(srv, "/api/settings").json()
    assert put({"_cookie_secret": "x"}).status_code == 400


def test_orphan_endpoints(srv, photo_root):
    import os
    sid = get(srv, "/api/sessions").json()["sessions"][0]["id"]
    requests.post(f"{srv.url}/api/sessions/{sid}/open", timeout=10)
    pid = get(srv, f"/api/sessions/{sid}/photos").json()["photos"][0]["id"]
    requests.put(f"{srv.url}/api/photos/{pid}/picked", json={"picked": True}, timeout=10)

    # 改名 + 删一张：自动模式不处理，留给手动
    os.rename(photo_root / SESSION1, photo_root / "renamed")
    (photo_root / "renamed" / "jpg" / "DSC00012.JPG").unlink()
    data = get(srv, "/api/sessions").json()
    assert data["adopted"] == [] and data["unmatched"] == 1
    orphans = get(srv, "/api/orphans").json()["orphans"]
    assert [o["id"] for o in orphans] == [sid] and orphans[0]["picked"] == 1

    post = lambda path, body: requests.post(f"{srv.url}{path}", json=body, timeout=10)
    assert post(f"/api/orphans/{sid}/adopt", {"target": "nope"}).status_code == 400
    r = post(f"/api/orphans/{sid}/adopt", {"target": "renamed"})
    assert r.json()["ok"] and r.json()["session"]["picked"] == 1
    data = get(srv, "/api/sessions").json()
    assert data["unmatched"] == 0
    assert {s["name"]: s["picked"] for s in data["sessions"]}["renamed"] == 1

    # 文件夹真的删掉：可以删除记录；文件夹还在的不能删
    other = [s for s in data["sessions"] if s["name"] == SESSION2][0]["id"]
    assert requests.delete(f"{srv.url}/api/orphans/{other}", timeout=10).status_code == 400
    import shutil
    shutil.rmtree(photo_root / "renamed")
    assert get(srv, "/api/sessions").json()["unmatched"] == 1
    assert requests.delete(f"{srv.url}/api/orphans/{sid}", timeout=10).json()["ok"]
    assert get(srv, "/api/sessions").json()["unmatched"] == 0
    assert get(srv, f"/api/photos/{pid}").status_code == 404


def test_full_flow(srv, photo_root):
    before = snapshot(photo_root)
    sid = get(srv, "/api/sessions").json()["sessions"][0]["id"]

    opened = requests.post(f"{srv.url}/api/sessions/{sid}/open", timeout=10).json()
    assert opened["scan"]["photos"] == 11 and opened["session"]["scanned"] is True

    photos = get(srv, f"/api/sessions/{sid}/photos").json()["photos"]
    assert len(photos) == 11
    p0 = photos[0]
    assert p0["name"] == "DSC00001.JPG" and p0["raw"] == "DSC00001.ARW" and p0["picked"] is False

    issues = get(srv, f"/api/sessions/{sid}/issues").json()["issues"]
    assert {i["kind"] for i in issues} == {"raw_only", "conflict", "subdir"}

    # 图片：带版本号时长期缓存
    r = get(srv, f"/img/{p0['id']}/preview?v={p0['v']}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert "immutable" in r.headers["cache-control"]
    assert get(srv, f"/img/{p0['id']}/thumb?v={p0['v']}").status_code == 200
    assert get(srv, f"/img/{p0['id']}/original?v={p0['v']}").content == \
        (photo_root / SESSION1 / "jpg" / "DSC00001.JPG").read_bytes()
    assert get(srv, f"/img/{p0['id']}/nope").status_code == 404

    # EXIF 详情
    detail = get(srv, f"/api/photos/{photos[1]['id']}").json()
    assert detail["exif"]["iso"].startswith("ISO ")

    # 标记
    for p in photos[:3]:
        r = requests.put(f"{srv.url}/api/photos/{p['id']}/picked", json={"picked": True}, timeout=10)
        assert r.json()["picked"] is True
    r = requests.put(f"{srv.url}/api/photos/{photos[2]['id']}/picked", json={"picked": False}, timeout=10)
    assert r.json()["picked_count"] == 2
    assert requests.put(f"{srv.url}/api/photos/{p0['id']}/picked", json={"picked": "yes"},
                        timeout=10).status_code == 400

    # 光标
    assert requests.put(f"{srv.url}/api/sessions/{sid}/cursor", json={"photo_id": photos[1]["id"]},
                        timeout=10).json()["ok"]
    assert get(srv, f"/api/sessions/{sid}/photos").json()["session"]["cursor"] == photos[1]["id"]

    # 导出
    plan = get(srv, f"/api/sessions/{sid}/export/plan").json()
    assert plan["copy"] == ["DSC00001.ARW", "DSC00002.ARW"] and plan["picked"] == 2
    job_id = requests.post(f"{srv.url}/api/sessions/{sid}/export", timeout=10).json()["job_id"]
    deadline = time.time() + 10
    while True:
        job = get(srv, f"/api/jobs/{job_id}").json()
        if job["status"] != "running" or time.time() > deadline:
            break
        time.sleep(0.05)
    assert job["status"] == "done"
    assert job["report"]["copied"] == ["DSC00001.ARW", "DSC00002.ARW"]
    assert sorted(p.name for p in (photo_root / SESSION1 / "select").iterdir()) == \
        ["DSC00001.ARW", "DSC00002.ARW"]
    assert snapshot(photo_root) == before

    # 预览状态最终全部完成
    deadline = time.time() + 20
    while time.time() < deadline:
        st = get(srv, f"/api/sessions/{sid}/status").json()
        if st["preview"]["ready"] == st["preview"]["total"]:
            break
        time.sleep(0.05)
    assert st["preview"] == {"total": 11, "ready": 11, "failed": 0}
    assert st["picked"] == 2 and st["export_job"] is None


def test_errors(srv):
    assert get(srv, "/api/sessions/999/photos").status_code == 404
    assert get(srv, "/api/photos/999").status_code == 404
    assert get(srv, "/img/999/preview").status_code == 404
    assert get(srv, "/api/jobs/999").status_code == 404
    r = requests.put(f"{srv.url}/api/photos/1/picked", data="{bad", timeout=10)
    assert r.status_code == 400


# ---------------------------------------------------------------- 下载原图（0.4.0）

def _photos(srv, name):
    sid = [s for s in get(srv, "/api/sessions").json()["sessions"] if s["name"] == name][0]["id"]
    requests.post(f"{srv.url}/api/sessions/{sid}/open", timeout=10)
    return get(srv, f"/api/sessions/{sid}/photos").json()["photos"]


def test_download_original(srv, photo_root):
    import shutil
    from urllib.parse import quote

    # 中文文件名：新建一个出门目录，放一对中文名的 JPG + RAW
    src_jpg = sorted((photo_root / SESSION1 / "jpg").glob("*.JPG"))[0]
    src_raw = sorted((photo_root / SESSION1 / "raw").glob("*.ARW"))[0]
    outing = photo_root / "20261119_下载测试"
    (outing / "jpg").mkdir(parents=True)
    (outing / "raw").mkdir()
    shutil.copy2(src_jpg, outing / "jpg" / "日落 01.JPG")
    shutil.copy2(src_raw, outing / "raw" / "日落 01.ARW")
    before = snapshot(photo_root)

    for name, folder in ((SESSION1, photo_root / SESSION1), ("20261119_下载测试", outing)):
        p = _photos(srv, name)[0]
        r = get(srv, f"/img/{p['id']}/download?v={p['v']}")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
        assert r.content == (folder / "jpg" / p["name"]).read_bytes()
        cd = r.headers["content-disposition"]
        assert cd.startswith("attachment")
        assert p["name"] in cd or quote(p["name"]) in cd
    # 下载是只读的
    assert snapshot(photo_root) == before

    assert get(srv, "/img/999999/download").status_code == 404
    # 原文件被移走：返回 404 和说明，而不是服务器错误
    p = _photos(srv, "20261119_下载测试")[0]
    (outing / "jpg" / "日落 01.JPG").unlink()
    for kind in ("download", "original"):
        r = get(srv, f"/img/{p['id']}/{kind}")
        assert r.status_code == 404 and "原始 JPG 不存在" in r.json()["error"]

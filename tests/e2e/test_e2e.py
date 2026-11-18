"""端到端测试：用真实浏览器走完整流程。

需要 playwright 和一个 Chromium 内核浏览器。默认找系统 Chrome，
也可以用环境变量 RAWPICK_CHROME 指定路径；都没有时用 playwright 自带的 Chromium，
再没有就跳过。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import SESSION1, SESSION2  # noqa: E402
from server_util import LiveServer  # noqa: E402

playwright = pytest.importorskip("playwright.sync_api")

IPAD_UA = ("Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
           "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")


def _chrome_path() -> str | None:
    env = os.environ.get("RAWPICK_CHROME")
    if env:
        return env
    for name in ("google-chrome", "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            return p
    return None


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        exe = _chrome_path()
        try:
            b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe \
                else p.chromium.launch()
        except Exception as e:  # pragma: no cover
            pytest.skip(f"没有可用的浏览器：{e}")
        yield b
        b.close()


@pytest.fixture
def srv(cfg):
    with LiveServer(cfg) as s:
        yield s


@pytest.fixture
def page(browser, srv):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(srv.url)
    yield pg
    ctx.close()
    assert errors == []


def open_session(pg, name=SESSION1):
    pg.wait_for_selector(".session-card")
    pg.click(f".session-card[data-name='{name}']")
    pg.wait_for_selector("[data-testid=name]:not(:empty)")


def text(pg, testid):
    return pg.text_content(f"[data-testid={testid}]")


def test_keyboard_culling_and_filters(page):
    open_session(page)
    assert text(page, "name") == "DSC00001.JPG"
    assert text(page, "pos") == "1 / 11"

    # 左手：F 选取、Q 取消；右手：Enter 选取、Backspace 取消。标记后自动跳到下一张
    for k in ("f", "q", "Enter", "Backspace", "d", "ArrowRight", "a"):
        page.keyboard.press(k)
    assert text(page, "count") == "已选 2 / 共 11"
    assert text(page, "pos") == "6 / 11"
    assert page.evaluate("location.hash").startswith("#/s/")  # Backspace 没有让页面后退

    # 只看已选
    page.click("[data-filter=picked]")
    assert text(page, "pos").endswith("/ 2")
    page.keyboard.press("ArrowLeft")
    assert text(page, "name") == "DSC00001.JPG"
    # 在过滤视图里取消：照片变灰留在原位，不立刻消失
    page.keyboard.press("q")
    page.keyboard.press("a")
    assert text(page, "name") == "DSC00001.JPG"
    assert "已不在当前过滤中" in page.text_content(".badges")
    assert text(page, "pos") == "1 / 2"
    # 再点一次过滤器才刷新
    page.click("[data-filter=picked]")
    assert text(page, "pos") == "1 / 1"
    assert text(page, "count") == "已选 1 / 共 11"

    page.click("[data-filter=unpicked]")
    assert text(page, "pos").endswith("/ 10")


def test_grid_navigation_and_layout(page):
    open_session(page)
    page.keyboard.press("g")
    page.wait_for_selector(".grid .cell")
    assert page.locator(".grid .cell").count() == 11
    assert page.get_attribute(".cell.focus", "data-i") == "0"
    page.keyboard.press("d")
    page.keyboard.press("Enter")  # 网格里 Enter 也是选取，并自动下移
    assert page.locator(".cell.picked").count() == 1
    assert page.get_attribute(".cell.focus", "data-i") == "2"
    page.keyboard.press("s")  # 下移一行
    cols = page.evaluate("[...document.querySelectorAll('.grid .cell')].filter(c => c.offsetTop === document.querySelector('.grid .cell').offsetTop).length")
    assert page.get_attribute(".cell.focus", "data-i") == str(min(2 + cols, 10))
    page.keyboard.press("w")
    assert page.get_attribute(".cell.focus", "data-i") == "2"

    # 布局：每个格子高度相同，图片（横幅和竖幅）完整落在格子里
    page.wait_for_function("[...document.querySelectorAll('.grid .cell img')].slice(0, 4).every(i => i.naturalWidth > 0)")
    boxes = page.evaluate("""() => [...document.querySelectorAll('.grid .cell')].slice(0, 4).map(c => {
        const b = c.getBoundingClientRect(), i = c.querySelector('img');
        return {h: b.height, w: b.width, iw: i.naturalWidth, ih: i.naturalHeight,
                ib: i.getBoundingClientRect().height};
    })""")
    heights = {round(b["h"]) for b in boxes}
    assert len(heights) == 1 and heights.pop() >= 120
    portrait = boxes[2]  # DSC00003 是竖拍
    assert portrait["ih"] > portrait["iw"]
    assert all(abs(b["ib"] - b["h"]) <= 4 for b in boxes)  # img 铺满格子，由 object-fit 负责缩放

    page.keyboard.press("e")
    assert text(page, "name") == "DSC00003.JPG"
    page.keyboard.press("g")
    page.click(".cell[data-i='5']")
    assert page.is_visible(".loupe") and text(page, "pos") == "6 / 11"


def test_zoom_and_preload(page, srv):
    previews: list[str] = []
    page.on("request", lambda r: previews.append(r.url) if "/preview?" in r.url else None)
    open_session(page)
    page.wait_for_function("document.querySelector('img.main').naturalWidth > 0")
    page.keyboard.press("z")
    page.wait_for_function("document.querySelector('img.orig').naturalWidth === 480")
    assert page.is_visible(".zoomwrap")
    page.keyboard.press("Space")  # 空格也能退出放大
    assert not page.is_visible(".zoomwrap")
    page.keyboard.press("Space")
    assert page.is_visible(".zoomwrap")
    page.keyboard.press("Escape")
    assert not page.is_visible(".zoomwrap")

    # 预加载：显示第 i 张时，第 i+1、i+2 张的大预览已请求
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(300)
    ids = page.evaluate("fetch('/api/sessions/' + location.hash.split('/')[2] + '/photos').then(r => r.json()).then(d => d.photos.map(p => p.id))")
    for pid in ids[1:4]:
        assert any(f"/img/{pid}/preview" in u for u in previews), pid


def test_help_panel(page):
    open_session(page)
    page.click("[data-testid=help-btn]")
    panel = page.locator("[data-testid=help-panel]")
    assert "Backspace" in panel.text_content() and "左右滑动" in panel.text_content()
    page.keyboard.press("Escape")
    assert page.locator(".backdrop").count() == 0
    page.keyboard.press("Shift+Slash")  # ? 键
    assert page.locator("[data-testid=help-panel]").count() == 1


def test_custom_shortcuts(page):
    page.click("text=设置")
    page.wait_for_selector(".keys-table")
    # 给“下一张”加一个 J
    page.click("tr[data-action=next] button[data-action=next]")
    page.keyboard.press("j")
    page.wait_for_selector("tr[data-action=next] kbd:text('J')")
    # 把 A 加到“下一张”：会从“上一张”上移走
    page.click("tr[data-action=next] button[data-action=next]")
    page.keyboard.press("a")
    page.wait_for_selector("tr[data-action=next] kbd:text('A')")
    assert page.locator("tr[data-action=prev] kbd:text('A')").count() == 0
    # 删除 D
    page.click("tr[data-action=next] .chip:has(kbd:text('D')) .chip-x")
    page.wait_for_function("!document.querySelector('tr[data-action=next]').textContent.includes('D×')")

    page.click("text=‹ 返回")
    open_session(page)
    page.keyboard.press("j")
    page.keyboard.press("a")
    assert text(page, "name") == "DSC00003.JPG"
    page.keyboard.press("d")  # 已删除，没反应
    assert text(page, "name") == "DSC00003.JPG"

    # 刷新后仍然生效（存在服务器上）；恢复默认
    page.reload()
    page.wait_for_selector("[data-testid=name]:not(:empty)")
    page.keyboard.press("j")
    assert text(page, "name") == "DSC00004.JPG"
    page.goto(page.url.split("#")[0] + "#/settings")
    page.click("text=恢复默认快捷键")
    page.wait_for_function("document.querySelector('tr[data-action=prev]').textContent.includes('A')")


def test_cursor_resumes_after_reload(page):
    open_session(page)
    for _ in range(3):
        page.keyboard.press("ArrowRight")
    assert text(page, "name") == "DSC00004.JPG"
    page.reload()  # 立刻刷新：离开页面时会马上保存位置，不用等延迟
    page.wait_for_selector("[data-testid=name]:not(:empty)")
    assert text(page, "name") == "DSC00004.JPG"


def test_export_flow(page, photo_root):
    open_session(page)
    page.keyboard.press("f")
    page.keyboard.press("f")
    page.click("text=导出")
    page.wait_for_selector("[data-testid=export-panel] .plan-row")
    assert "2 张" in page.text_content(".plan-details:has-text('将拷贝')")
    page.click("text=开始导出")
    page.wait_for_selector("text=导出完成", timeout=10000)
    sel = photo_root / SESSION1 / "select"
    assert sorted(p.name for p in sel.iterdir()) == ["DSC00001.ARW", "DSC00002.ARW"]
    page.keyboard.press("Escape")
    assert page.locator(".backdrop").count() == 0
    # 回到首页显示“已导出 2”
    page.click("text=‹ 返回")
    page.wait_for_selector(f".session-card[data-name='{SESSION1}'] .status-exported")
    assert "已导出 2" in page.text_content(f".session-card[data-name='{SESSION1}']")


def test_issues_panel(page):
    open_session(page)
    page.wait_for_selector(".warn-btn:not([hidden])")
    page.click(".warn-btn")
    body = page.text_content(".modal-body")
    assert "DSC09999.ARW" in body and "jpg/extra" in body and "DSC00007.jpeg" in body


def test_home_sort_filter_view(page, photo_root):
    import shutil
    for name in ("20250101_Alpha", "20261231_Zulu", "20250615_Beta"):
        shutil.copytree(photo_root / SESSION2, photo_root / name)
    (photo_root / "20250615_Beta" / "select").mkdir()
    (photo_root / "20250615_Beta" / "select" / "x.ARW").write_bytes(b"1")
    page.click("text=刷新")
    page.wait_for_selector(".session-card[data-name='20250101_Alpha']")
    cards = lambda: page.eval_on_selector_all(".session-card", "els => els.map(e => e.dataset.name)")
    # 默认：日期新→旧，不符合格式的排最后
    assert cards()[:3] == ["20261231_Zulu", "20250615_Beta", "20250101_Alpha"]
    assert "2025-06-15" in page.text_content(".session-card[data-name='20250615_Beta']")

    page.fill("input[type=search]", "beta")
    assert cards() == ["20250615_Beta"]
    page.fill("input[type=search]", "")
    page.select_option("select[aria-label=年份]", "2025")
    assert set(cards()) == {"20250101_Alpha", "20250615_Beta"}
    page.select_option("select[aria-label=年份]", "")
    page.click("[data-status=exported]")
    assert cards() == ["20250615_Beta"]
    page.click("[data-status=all]")

    page.select_option("select[aria-label=排序]", "topic")
    mine = [c for c in cards() if c[:2] == "20" and "_" in c]
    assert mine == ["20250101_Alpha", "20250615_Beta", "20261231_Zulu"]
    page.click("[data-view=list]")
    assert page.locator(".session-table tr.row-link").count() == 5

    # 排序和视图记在这台设备上，刷新后还在
    page.reload()
    page.wait_for_selector(".session-table tr.row-link")
    rows = page.eval_on_selector_all(".session-table tr.row-link", "els => els.map(e => e.dataset.name)")
    assert [r for r in rows if r[:2] == "20" and "_" in r][0] == "20250101_Alpha"
    page.click(".session-table tr.row-link[data-name='20250101_Alpha'] td.num >> nth=0")
    page.wait_for_selector("[data-testid=name]:not(:empty)")


def test_relink_after_rename(page, photo_root):
    import os
    import shutil
    open_session(page)
    page.keyboard.press("f")
    page.wait_for_timeout(200)
    page.click("text=‹ 返回")
    page.wait_for_selector(".session-card")
    # 改名并删一张：自动模式不处理，首页出现提示
    os.rename(photo_root / SESSION1, photo_root / "20261108_改名")
    (photo_root / "20261108_改名" / "jpg" / "DSC00012.JPG").unlink()
    page.click("text=刷新")
    page.wait_for_selector(".notice:not([hidden])")
    page.click(".notice a")
    page.wait_for_selector(f".orphan[data-name='{SESSION1}'] select")
    page.select_option(f".orphan[data-name='{SESSION1}'] select", "20261108_改名")
    page.once("dialog", lambda d: d.accept())  # 同名照片不全时会确认
    page.click(f".orphan[data-name='{SESSION1}'] button.primary")
    page.wait_for_selector("text=没有需要处理的记录")
    page.click("text=‹ 返回")
    page.wait_for_selector(".session-card[data-name='20261108_改名']")
    assert "已选 1" in page.text_content(".session-card[data-name='20261108_改名']")
    assert page.is_hidden(".notice")

    # 文件夹真的删掉：在设置里删除记录
    shutil.rmtree(photo_root / "20261108_改名")
    page.click("text=刷新")
    page.wait_for_selector(".notice:not([hidden])")
    page.click(".notice a")
    page.once("dialog", lambda d: d.accept())
    page.click(".orphan[data-name='20261108_改名'] >> text=删除这条记录")
    page.wait_for_selector("text=没有需要处理的记录")


def test_ipad_touch(browser, srv):
    ctx = browser.new_context(viewport={"width": 820, "height": 1180}, device_scale_factor=2,
                              has_touch=True, is_mobile=True, user_agent=IPAD_UA)
    pg = ctx.new_page()
    pg.goto(srv.url)
    open_session(pg)
    pg.tap("[data-testid=pick-btn]")
    assert text(pg, "count") == "已选 1 / 共 11"
    assert text(pg, "name") == "DSC00002.JPG"

    # 模拟手指左滑：下一张；右滑：上一张
    swipe = """([x0, x1]) => {
        const el = document.querySelector('.stage');
        const mk = (x) => new Touch({identifier: 1, target: el, clientX: x, clientY: 400});
        el.dispatchEvent(new TouchEvent('touchstart', {touches: [mk(x0)], changedTouches: [mk(x0)], bubbles: true, cancelable: true}));
        el.dispatchEvent(new TouchEvent('touchmove', {touches: [mk(x1)], changedTouches: [mk(x1)], bubbles: true, cancelable: true}));
        el.dispatchEvent(new TouchEvent('touchend', {touches: [], changedTouches: [mk(x1)], bubbles: true, cancelable: true}));
    }"""
    pg.evaluate(swipe, [600, 200])
    assert text(pg, "name") == "DSC00003.JPG"
    pg.evaluate(swipe, [200, 600])
    assert text(pg, "name") == "DSC00002.JPG"
    pg.tap("[data-testid=help-btn]")
    assert pg.locator("[data-testid=help-panel]").count() == 1
    ctx.close()


# ---------------------------------------------------------------- 客户相册

PHONE = dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)


def test_publish_select_submit_export(browser, srv, page, photo_root):
    open_session(page)
    for _ in range(3):
        page.keyboard.press("f")
    page.click("[data-testid=publish-btn]")
    page.wait_for_selector("[data-testid=publish-submit]")
    assert "3 张已选照片" in page.text_content("[data-testid=publish-dialog]")
    page.fill("input[name=client_name]", "王先生")
    page.fill("input[name=limit_count]", "2")
    page.click("text=需要密码")
    page.click("[data-testid=publish-submit]")
    assert page.text_content("[data-testid=publish-dialog] .error") == "请填写密码，或者选择公开"
    page.fill("input[name=password]", "2468")
    page.click("[data-testid=publish-submit]")
    page.wait_for_selector("[data-testid=gallery-link]")
    page.wait_for_function("document.querySelector('.status').textContent === '选片中'", timeout=15000)
    link = page.input_value("[data-testid=gallery-link]")
    assert link.startswith(srv.client_url + "/g/")

    ctx = browser.new_context(locale="zh-CN", **PHONE)
    c = ctx.new_page()
    errors: list[str] = []
    c.on("pageerror", lambda e: errors.append(str(e)))
    c.goto(link)
    c.fill("[data-testid=password]", "1111")
    c.click("button[type=submit]")
    c.wait_for_selector(".center-card .error:not([hidden])")
    c.fill("[data-testid=password]", "2468")
    c.click("button[type=submit]")
    c.wait_for_selector("[data-testid=welcome]")
    c.click("[data-testid=welcome] .btn.primary")
    assert c.text_content("[data-testid=count]") == "已选 0 / 可选 2"
    c.tap(".cell[data-i='0']")
    c.wait_for_selector("[data-testid=pick-btn]:visible")
    for _ in range(3):  # 选 3 张：选的时候不拦
        c.tap("[data-testid=pick-btn]")
        c.click("button[aria-label='下一张']")
    assert c.text_content("[data-testid=count]") == "已选 3 / 可选 2"
    c.click("button[aria-label='上一张']")
    c.tap("[data-testid=note-btn]")
    c.fill("[data-testid=note-dialog] textarea", "去掉路人")
    c.click("[data-testid=note-dialog] .btn.primary")
    c.wait_for_selector(".badge.note")
    # 超过上限，不能提交
    c.click("[data-testid=submit-btn]")
    assert "请先取消 1 张" in c.text_content("[data-testid=submit-dialog]")
    c.click("[data-testid=submit-dialog] .btn")
    c.click("button[aria-label='上一张']")
    c.tap("[data-testid=pick-btn]")  # 取消第 2 张（第 3 张有备注，保留）
    assert c.text_content("[data-testid=count]") == "已选 2 / 可选 2"
    c.click("[data-testid=submit-btn]")
    c.click("[data-testid=submit-confirm]")
    c.wait_for_selector("[data-testid=submit-btn]:disabled")
    assert "提交选择" in c.text_content("[data-testid=client-notice]")
    ctx.close()
    assert errors == []

    page.reload()
    page.wait_for_selector("[data-testid=gallery-summary]")
    assert "客户已选 2 张" in page.text_content("[data-testid=gallery-summary]")
    assert page.text_content(".gphoto-note") == "去掉路人"
    dest = page.input_value("[data-testid=export-dir]")
    page.click("[data-testid=gallery-export]")
    page.wait_for_selector("[data-testid=export-panel] .btn.primary:not([disabled])")
    page.click("[data-testid=export-panel] .btn.primary")
    page.wait_for_selector("text=导出完成")
    assert len(list((photo_root / SESSION1 / dest).iterdir())) == 2


def test_client_delivery_and_english(browser, srv, photo_root):
    import requests
    import shutil as sh

    sid = [s for s in requests.get(srv.url + "/api/sessions").json()["sessions"] if s["name"] == SESSION2][0]["id"]
    requests.post(f"{srv.url}/api/sessions/{sid}/open")
    photos = requests.get(f"{srv.url}/api/sessions/{sid}/photos").json()["photos"]
    requests.put(f"{srv.url}/api/photos/{photos[0]['id']}/picked", json={"picked": True})
    g = requests.post(f"{srv.url}/api/sessions/{sid}/galleries",
                      json={"title": "Final", "delete_at": 1893456000}).json()
    final = photo_root / SESSION2 / "final"
    final.mkdir()
    for f in sorted((photo_root / SESSION2 / "jpg").glob("*.JPG"))[:2]:
        sh.copy2(f, final / f.name)
    srv.app.state.publisher.wait()
    requests.post(f"{srv.url}/api/galleries/{g['id']}/delivery", json={"folder": "final"})
    srv.app.state.publisher.wait()

    ctx = browser.new_context(locale="en-US", **PHONE)
    c = ctx.new_page()
    c.goto(g["link"])
    c.wait_for_selector("[data-testid=welcome]")
    assert "This gallery will be removed on" in c.text_content("[data-testid=welcome]")
    c.click("[data-testid=welcome] .btn.primary")
    assert c.text_content("[data-testid=count]") == "0 selected"
    c.click("[data-tab=final]")
    assert c.get_attribute("[data-testid=zip-link]", "href").endswith("/d/all.zip")
    assert "2 edited photos" in c.text_content("[data-testid=client-notice]")
    c.tap(".body:not([hidden]) .cell[data-i='1']")
    href = c.get_attribute("[data-testid=download-link]", "href")
    r = c.request.get(srv.client_url + href)
    assert r.body() == sorted(final.iterdir())[1].read_bytes()
    ctx.close()

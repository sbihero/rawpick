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

from conftest import SESSION1  # noqa: E402
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


def open_session(pg):
    pg.wait_for_selector(".session-card")
    pg.click(f".session-card:has-text('{SESSION1}')")
    pg.wait_for_selector("[data-testid=name]:not(:empty)")


def text(pg, testid):
    return pg.text_content(f"[data-testid={testid}]")


def test_keyboard_culling_and_filters(page):
    open_session(page)
    assert text(page, "name") == "DSC00001.JPG"
    assert text(page, "pos") == "1 / 11"

    # P 选中、X 取消，标记后自动跳到下一张
    for k in ("p", "x", "p"):
        page.keyboard.press(k)
    assert text(page, "count") == "已选 2 / 共 11"
    assert text(page, "pos") == "4 / 11"

    # 只看已选
    page.click("[data-filter=picked]")
    assert text(page, "pos").endswith("/ 2")
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowLeft")
    assert text(page, "name") == "DSC00001.JPG"
    # 在过滤视图里取消：照片变灰留在原位，不立刻消失
    page.keyboard.press("x")
    page.keyboard.press("ArrowLeft")
    assert text(page, "name") == "DSC00001.JPG"
    assert "已不在当前过滤中" in page.text_content(".badges")
    assert text(page, "pos") == "1 / 2"
    # 再点一次过滤器才刷新
    page.click("[data-filter=picked]")
    assert text(page, "pos") == "1 / 1"
    assert text(page, "count") == "已选 1 / 共 11"

    page.click("[data-filter=unpicked]")
    assert text(page, "pos").endswith("/ 10")


def test_grid_navigation(page):
    open_session(page)
    page.keyboard.press("g")
    page.wait_for_selector(".grid .cell")
    assert page.locator(".grid .cell").count() == 11
    assert page.get_attribute(".cell.focus", "data-i") == "0"
    page.keyboard.press("ArrowRight")
    page.keyboard.press("p")  # 网格里也能标记，并自动下移
    assert page.locator(".cell.picked").count() == 1
    assert page.get_attribute(".cell.focus", "data-i") == "2"
    page.keyboard.press("Enter")
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
    page.keyboard.press("Escape")
    assert not page.is_visible(".zoomwrap")

    # 预加载：显示第 i 张时，第 i+1、i+2 张的大预览已请求
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(300)
    ids = page.evaluate("fetch('/api/sessions/1/photos').then(r => r.json()).then(d => d.photos.map(p => p.id))")
    for pid in ids[1:4]:
        assert any(f"/img/{pid}/preview" in u for u in previews), pid


def test_cursor_resumes_after_reload(page):
    open_session(page)
    for _ in range(3):
        page.keyboard.press("ArrowRight")
    assert text(page, "name") == "DSC00004.JPG"
    page.wait_for_timeout(1200)  # 光标保存有 0.8 秒延迟
    page.reload()
    page.wait_for_selector("[data-testid=name]:not(:empty)")
    assert text(page, "name") == "DSC00004.JPG"


def test_export_flow(page, photo_root):
    open_session(page)
    page.keyboard.press("p")
    page.keyboard.press("p")
    page.click("text=导出")
    page.wait_for_selector("[data-testid=export-panel] .plan-row")
    assert "2 张" in page.text_content(".plan-details:has-text('将拷贝')")
    page.click("text=开始导出")
    page.wait_for_selector("text=导出完成", timeout=10000)
    sel = photo_root / SESSION1 / "select"
    assert sorted(p.name for p in sel.iterdir()) == ["DSC00001.ARW", "DSC00002.ARW"]
    page.keyboard.press("Escape")
    assert page.locator(".backdrop").count() == 0


def test_issues_panel(page):
    open_session(page)
    page.wait_for_selector(".warn-btn:not([hidden])")
    page.click(".warn-btn")
    body = page.text_content(".modal-body")
    assert "DSC09999.ARW" in body and "jpg/extra" in body and "DSC00007.jpeg" in body


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
    ctx.close()

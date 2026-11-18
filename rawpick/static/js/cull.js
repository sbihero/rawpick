// 筛选界面：顶栏（过滤、视图、导出、发布相册）+ 共用的照片浏览组件。
import { api, imgUrl } from "./api.js";
import { PhotoBrowser } from "./browser.js";
import { openExportPanel } from "./exportPanel.js";
import { showHelp } from "./help.js";
import { openPublishDialog } from "./publish.js";
import { h, nameList, openModal } from "./util.js";
const FILTER_LABEL = { all: "全部", picked: "已选", unpicked: "未选" };
const ISSUE_LABEL = {
    raw_only: "只有 RAW、没有 JPG（不参与筛选）",
    conflict: "同一文件名有多个文件（未参与配对）",
    subdir: "被忽略的子目录",
};
export class CullView {
    root;
    sid;
    session = null;
    modalOpen = false;
    issues = [];
    exifPending = new Set();
    cleanups = [];
    pollTimer = 0;
    cursorTimer = 0;
    pendingCursor = null;
    browser;
    titleEl = h("div", { class: "title" });
    progressEl = h("span", { class: "progress muted" });
    issuesBtn = h("button", { class: "btn small warn-btn", hidden: true });
    countEl = h("span", { class: "count", "data-testid": "count" });
    filterBtns = {};
    modeBtns = {};
    constructor(root, sid) {
        this.root = root;
        this.sid = sid;
        const body = this.build();
        this.browser = new PhotoBrowser(body, {
            text: {
                pick: "选中", picked: "✓ 已选", pickedBadge: "已选", prev: "上一张", next: "下一张", zoom: "放大",
                first: "已经是第一张", last: "已经是最后一张", empty: "这个文件夹里没有照片",
                emptyFilter: (f) => `没有${FILTER_LABEL[f]}的照片`, loadingOriginal: "加载原图……",
                zoomHint: "100% · 再按 Z 或 Esc 退出", outOfFilter: "已不在当前过滤中", checkMark: "✓",
            },
            autoAdvance: true,
            showPick: true,
            canPick: () => true,
            save: (p, picked) => api.setPicked(p.id, picked).catch((e) => {
                throw new Error(`保存失败：${e.message}`);
            }),
            describe: (p) => this.describe(p),
            onRender: () => this.renderHeader(),
            onCurrent: (p) => this.saveCursorSoon(p),
            onAction: (a) => { if (a === "help")
                this.openHelp(); },
            modalOpen: () => this.modalOpen,
        });
        this.bindEvents();
        void this.load();
    }
    destroy() {
        window.clearTimeout(this.pollTimer);
        this.flushCursor();
        this.browser.destroy();
        for (const fn of this.cleanups)
            fn();
    }
    // ------------------------------------------------------------------ 构建
    build() {
        const filters = h("div", { class: "seg", role: "group", "aria-label": "过滤" });
        for (const f of ["all", "picked", "unpicked"]) {
            const b = h("button", { class: "seg-btn", text: FILTER_LABEL[f], "data-filter": f,
                onclick: () => this.browser.setFilter(f) });
            this.filterBtns[f] = b;
            filters.append(b);
        }
        const modes = h("div", { class: "seg", role: "group", "aria-label": "视图" });
        for (const [m, label] of [["loupe", "单张"], ["grid", "网格"]]) {
            const b = h("button", { class: "seg-btn", text: label, onclick: () => this.browser.setMode(m) });
            this.modeBtns[m] = b;
            modes.append(b);
        }
        this.issuesBtn.addEventListener("click", () => this.showIssues());
        const header = h("header", { class: "topbar" }, h("a", { class: "btn back", href: "#/", text: "‹ 返回" }), this.titleEl, h("div", { class: "spacer" }), this.progressEl, this.issuesBtn, this.countEl, filters, modes, h("button", { class: "btn help-btn", text: "?", title: "操作说明", "aria-label": "操作说明",
            "data-testid": "help-btn", onclick: () => this.openHelp() }), h("button", { class: "btn", text: "发布相册", "data-testid": "publish-btn", onclick: () => this.openPublish() }), h("button", { class: "btn primary", text: "导出", onclick: () => this.openExport() }));
        const body = h("div", { class: "body" });
        this.root.replaceChildren(header, body);
        this.root.classList.add("cull-root");
        this.cleanups.push(() => this.root.classList.remove("cull-root"));
        this.titleEl.textContent = "正在扫描……";
        return body;
    }
    bindEvents() {
        const onHide = () => this.flushCursor();
        const onVis = () => { if (document.visibilityState === "hidden")
            this.flushCursor(); };
        window.addEventListener("pagehide", onHide);
        document.addEventListener("visibilitychange", onVis);
        this.cleanups.push(() => {
            window.removeEventListener("pagehide", onHide);
            document.removeEventListener("visibilitychange", onVis);
        });
    }
    // ------------------------------------------------------------------ 加载
    async load() {
        let photos;
        try {
            await api.open(this.sid);
            const data = await api.photos(this.sid);
            this.session = data.session;
            photos = data.photos;
        }
        catch (e) {
            this.titleEl.textContent = "";
            this.browser.setLoading(`打开失败：${e.message}`);
            return;
        }
        this.titleEl.textContent = this.session.name;
        document.title = `${this.session.name} · rawpick`;
        const items = photos.map((p) => Object.assign(p, {
            thumb: imgUrl(p, "thumb"), preview: imgUrl(p, "preview"), original: imgUrl(p, "original"),
        }));
        this.browser.setItems(items, this.session.cursor);
        void this.pollStatus();
        api.issues(this.sid).then((r) => {
            this.issues = r.issues;
            this.issuesBtn.hidden = r.issues.length === 0;
            this.issuesBtn.textContent = `异常 ${r.issues.length}`;
        }, () => { });
    }
    async pollStatus() {
        try {
            const st = await api.status(this.sid);
            const { total, ready, failed } = st.preview;
            if (ready + failed < total) {
                this.progressEl.textContent = `生成预览 ${ready}/${total}`;
                this.pollTimer = window.setTimeout(() => this.pollStatus(), 1000);
            }
            else {
                this.progressEl.textContent = failed ? `${failed} 张预览失败` : "";
            }
        }
        catch {
            this.pollTimer = window.setTimeout(() => this.pollStatus(), 5000);
        }
    }
    // ------------------------------------------------------------------ 渲染
    renderHeader() {
        const b = this.browser;
        if (!b)
            return;
        this.countEl.textContent = `已选 ${b.pickedCount()} / 共 ${b.items.length}`;
        for (const f of Object.keys(this.filterBtns)) {
            this.filterBtns[f].classList.toggle("active", f === b.filter);
        }
        for (const m of Object.keys(this.modeBtns)) {
            this.modeBtns[m].classList.toggle("active", m === b.mode);
        }
    }
    describe(p) {
        const badges = [];
        if (!p.raw)
            badges.push(h("span", { class: "badge noraw", text: "缺 RAW" }));
        if (p.error)
            badges.push(h("span", { class: "badge noraw", text: "预览失败" }));
        let exif = "";
        if (p.exif) {
            const e = p.exif;
            exif = [e.shutter, e.aperture, e.iso, e.focal].filter(Boolean).join(" · ");
        }
        else {
            this.fetchExif(p);
        }
        return { badges, exif, cell: { noraw: !p.raw } };
    }
    fetchExif(p) {
        if (this.exifPending.has(p.id))
            return;
        this.exifPending.add(p.id);
        api.photo(p.id).then((full) => {
            p.exif = full.exif || {};
            if (this.browser.current() === p)
                this.browser.refreshCurrent();
        }, () => { }).finally(() => this.exifPending.delete(p.id));
    }
    // ------------------------------------------------------------------ 进度
    saveCursorSoon(p) {
        window.clearTimeout(this.cursorTimer);
        if (!p)
            return;
        this.pendingCursor = p.id;
        this.cursorTimer = window.setTimeout(() => this.flushCursor(), 800);
    }
    /** 立即保存还没发出去的位置（切换页面、刷新、关闭标签页时调用）。 */
    flushCursor() {
        window.clearTimeout(this.cursorTimer);
        if (this.pendingCursor === null)
            return;
        const id = this.pendingCursor;
        this.pendingCursor = null;
        api.cursor(this.sid, id, true).catch(() => { });
    }
    // ------------------------------------------------------------------ 弹窗
    afterModal() {
        this.modalOpen = false;
    }
    openExport() {
        this.modalOpen = true;
        const target = {
            title: "导出到 select/",
            dest: "select",
            plan: () => api.plan(this.sid),
            start: () => api.startExport(this.sid),
        };
        void api.status(this.sid).then((st) => openExportPanel(target, st.export_job, () => this.afterModal()), () => openExportPanel(target, null, () => this.afterModal()));
    }
    openPublish() {
        if (!this.session)
            return;
        this.modalOpen = true;
        openPublishDialog(this.session, this.browser.pickedCount(), () => this.afterModal());
    }
    openHelp() {
        this.modalOpen = true;
        showHelp(() => this.afterModal());
    }
    showIssues() {
        this.modalOpen = true;
        const m = openModal("扫描发现的异常", () => this.afterModal());
        const groups = new Map();
        for (const i of this.issues) {
            if (!groups.has(i.kind))
                groups.set(i.kind, []);
            groups.get(i.kind).push(i.name);
        }
        for (const [kind, names] of groups) {
            m.body.append(h("h3", { text: `${ISSUE_LABEL[kind]}（${names.length}）` }), nameList(names));
        }
        m.actions.append(h("button", { class: "btn", text: "关闭", onclick: () => m.close() }));
    }
}

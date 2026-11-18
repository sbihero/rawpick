// 首页：出门目录列表，支持搜索、年份和状态筛选、排序、卡片 / 列表两种视图。
// 文件夹名按 yyyymmdd_主题 解析出日期和主题；不符合格式的照常显示，排在最后。
import { api } from "./api.js";
import { fmtTime, h, toast } from "./util.js";
const SORT_LABEL = {
    date_desc: "日期：新→旧", date_asc: "日期：旧→新", topic: "主题：A→Z", count: "照片张数：多→少",
};
const STATUS_LABEL = { new: "未打开", opened: "筛选中", exported: "已导出" };
const PREF_KEY = "rawpick.home.v1";
export function parseName(name) {
    const m = name.match(/^(\d{4})(\d{2})(\d{2})_(.+)$/);
    if (m) {
        const mo = Number(m[2]);
        const d = Number(m[3]);
        if (mo >= 1 && mo <= 12 && d >= 1 && d <= 31) {
            return { date: `${m[1]}-${m[2]}-${m[3]}`, year: m[1], topic: m[4] };
        }
    }
    return { date: null, year: null, topic: name };
}
function loadPrefs() {
    const def = { sort: "date_desc", view: "cards" };
    try {
        const p = JSON.parse(localStorage.getItem(PREF_KEY) || "{}");
        return {
            sort: p.sort in SORT_LABEL ? p.sort : def.sort,
            view: p.view === "list" || p.view === "cards" ? p.view : def.view,
        };
    }
    catch {
        return def;
    }
}
function savePrefs(p) {
    try {
        localStorage.setItem(PREF_KEY, JSON.stringify(p));
    }
    catch {
        /* 隐私模式等情况下存不了，忽略 */
    }
}
const filters = { q: "", year: "", status: "all" };
const collator = new Intl.Collator(["zh-Hans-CN", "en"], { numeric: true, sensitivity: "base" });
export class HomeView {
    root;
    prefs = loadPrefs();
    items = [];
    root_path = "";
    loading = 0;
    cleanups = [];
    listEl = h("div", { class: "home-list" });
    summaryEl = h("div", { class: "home-summary muted small" });
    noticeEl = h("div", { class: "notice", hidden: true });
    yearSel = h("select", { class: "select", "aria-label": "年份" });
    refreshBtn = h("button", { class: "btn", text: "刷新" });
    constructor(root) {
        this.root = root;
        document.title = "rawpick";
        this.build();
        void this.load();
        // 标签页回到前台时自动刷新（比如刚在 Windows 上拷完照片）
        const onVis = () => {
            if (document.visibilityState === "visible")
                void this.load();
        };
        document.addEventListener("visibilitychange", onVis);
        this.cleanups.push(() => document.removeEventListener("visibilitychange", onVis));
    }
    destroy() {
        for (const fn of this.cleanups)
            fn();
    }
    build() {
        const search = h("input", {
            class: "input", type: "search", placeholder: "搜索主题或文件夹名", value: filters.q,
            "aria-label": "搜索",
        });
        search.addEventListener("input", () => { filters.q = search.value; this.renderList(); });
        this.yearSel.addEventListener("change", () => { filters.year = this.yearSel.value; this.renderList(); });
        const statusSeg = h("div", { class: "seg", role: "group", "aria-label": "状态" });
        for (const st of ["all", "new", "opened", "exported"]) {
            const b = h("button", { class: "seg-btn", "data-status": st,
                text: st === "all" ? "全部" : STATUS_LABEL[st] });
            b.addEventListener("click", () => { filters.status = st; this.renderList(); });
            statusSeg.append(b);
        }
        const sortSel = h("select", { class: "select", "aria-label": "排序" });
        for (const [k, label] of Object.entries(SORT_LABEL)) {
            sortSel.append(h("option", { value: k, text: label, selected: k === this.prefs.sort }));
        }
        sortSel.addEventListener("change", () => {
            this.prefs.sort = sortSel.value;
            savePrefs(this.prefs);
            this.renderList();
        });
        const viewSeg = h("div", { class: "seg", role: "group", "aria-label": "视图" });
        for (const [v, label] of [["cards", "卡片"], ["list", "列表"]]) {
            const b = h("button", { class: "seg-btn", "data-view": v, text: label });
            b.addEventListener("click", () => { this.prefs.view = v; savePrefs(this.prefs); this.renderList(); });
            viewSeg.append(b);
        }
        this.refreshBtn.addEventListener("click", () => void this.load(true));
        this.root.replaceChildren(h("header", { class: "topbar" }, h("div", { class: "brand", text: "rawpick" }), h("div", { class: "spacer" }), this.refreshBtn, h("a", { class: "btn", href: "#/galleries", text: "客户相册", "data-testid": "galleries-link" }), h("a", { class: "btn", href: "#/settings", text: "⚙ 设置", "aria-label": "设置" })), h("main", { class: "home" }, this.noticeEl, h("div", { class: "home-tools" }, search, this.yearSel, statusSeg, sortSel, viewSeg), this.summaryEl, this.listEl));
    }
    async load(manual = false) {
        const ticket = ++this.loading;
        if (manual) {
            this.refreshBtn.disabled = true;
            this.refreshBtn.textContent = "刷新中……";
        }
        if (this.items.length === 0)
            this.listEl.replaceChildren(h("p", { class: "muted", text: "正在读取照片目录……" }));
        try {
            const data = await api.sessions();
            if (ticket !== this.loading)
                return; // 已有更新的请求
            this.root_path = data.root;
            this.items = data.sessions.map((s) => ({ s, ...parseName(s.name) }));
            for (const a of data.adopted ?? [])
                toast(`已自动关联标记：${a.from} → ${a.to}`, 5000);
            this.renderNotice(data.unmatched ?? 0);
            this.renderYears();
            this.renderList();
        }
        catch (e) {
            if (ticket !== this.loading)
                return;
            this.listEl.replaceChildren(h("p", { class: "error", text: `读取失败：${e.message}` }));
        }
        finally {
            if (manual) {
                this.refreshBtn.disabled = false;
                this.refreshBtn.textContent = "刷新";
            }
        }
    }
    renderNotice(unmatched) {
        this.noticeEl.hidden = unmatched === 0;
        if (unmatched > 0) {
            this.noticeEl.replaceChildren(`有 ${unmatched} 个文件夹的标记找不到对应的文件夹（可能改名或移动过）。`, h("a", { href: "#/settings", text: "去处理" }));
        }
    }
    renderYears() {
        const years = [...new Set(this.items.map((i) => i.year).filter((y) => !!y))]
            .sort().reverse();
        if (filters.year && !years.includes(filters.year))
            filters.year = "";
        this.yearSel.replaceChildren(h("option", { value: "", text: "全部年份" }), ...years.map((y) => h("option", { value: y, text: `${y} 年`, selected: y === filters.year })));
    }
    visibleItems() {
        const q = filters.q.trim().toLowerCase();
        const out = this.items.filter((i) => (!q || i.s.name.toLowerCase().includes(q) || i.topic.toLowerCase().includes(q))
            && (!filters.year || i.year === filters.year)
            && (filters.status === "all" || i.s.status === filters.status));
        const byName = (a, b) => collator.compare(b.s.name, a.s.name);
        const nullsLast = (a, b) => (a.date === null ? 1 : 0) - (b.date === null ? 1 : 0);
        switch (this.prefs.sort) {
            case "date_desc":
                out.sort((a, b) => nullsLast(a, b) || (b.date ?? "").localeCompare(a.date ?? "") || byName(a, b));
                break;
            case "date_asc":
                out.sort((a, b) => nullsLast(a, b) || (a.date ?? "").localeCompare(b.date ?? "") || -byName(a, b));
                break;
            case "topic":
                out.sort((a, b) => collator.compare(a.topic, b.topic) || byName(a, b));
                break;
            case "count":
                out.sort((a, b) => b.s.total - a.s.total || byName(a, b));
                break;
        }
        return out;
    }
    statusBadge(s) {
        const text = s.status === "exported" ? `已导出 ${s.select_count}` : STATUS_LABEL[s.status];
        return h("span", { class: `status status-${s.status}`, text });
    }
    countText(s) {
        return s.scanned ? `${s.total} 张` : `约 ${s.total} 张`;
    }
    renderList() {
        this.root.querySelectorAll("[data-status]").forEach((b) => b.classList.toggle("active", b.dataset.status === filters.status));
        this.root.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === this.prefs.view));
        const items = this.visibleItems();
        this.summaryEl.textContent =
            `共 ${this.items.length} 个文件夹` +
                (items.length !== this.items.length ? `，显示 ${items.length} 个` : "") +
                ` · 照片根目录：${this.root_path}`;
        if (this.items.length === 0) {
            this.listEl.replaceChildren(h("p", { class: "muted",
                text: "没有找到同时包含 jpg/ 和 raw/ 子目录的文件夹。" }));
            return;
        }
        if (items.length === 0) {
            this.listEl.replaceChildren(h("p", { class: "muted", text: "没有符合条件的文件夹。" }));
            return;
        }
        this.listEl.replaceChildren(this.prefs.view === "cards" ? this.cards(items) : this.table(items));
    }
    cards(items) {
        const box = h("div", { class: "sessions" });
        for (const { s, date, topic } of items) {
            box.append(h("a", { class: "session-card", href: `#/s/${s.id}`, "data-name": s.name }, h("div", { class: "session-date muted small", text: date ?? "未识别日期" }), h("div", { class: "session-name", text: topic }), h("div", { class: "session-meta" }, h("span", { text: `${this.countText(s)} · 已选 ${s.picked}` }), this.statusBadge(s)), s.last_export_at
                ? h("div", { class: "session-meta muted small", text: `上次导出 ${fmtTime(s.last_export_at)}` })
                : null));
        }
        return box;
    }
    table(items) {
        // hide-sm：手机上不显示的列
        const head = h("tr", {}, ...[["日期", ""], ["主题", ""], ["张数", "hide-sm"], ["已选", ""], ["状态", ""],
            ["上次导出", "hide-sm"]].map(([t, c]) => h("th", { class: c, text: t })));
        const rows = items.map(({ s, date, topic }) => {
            const tr = h("tr", { class: "row-link", "data-name": s.name }, h("td", { class: "nowrap", text: date ?? "—" }), h("td", {}, h("a", { href: `#/s/${s.id}`, text: topic })), h("td", { class: "num hide-sm", text: this.countText(s) }), h("td", { class: "num", text: String(s.picked) }), h("td", { class: "nowrap" }, this.statusBadge(s)), h("td", { class: "nowrap muted hide-sm", text: s.last_export_at ? fmtTime(s.last_export_at) : "" }));
            tr.addEventListener("click", (e) => {
                if (!e.target.closest("a"))
                    location.hash = `#/s/${s.id}`;
            });
            return tr;
        });
        return h("table", { class: "session-table" }, h("thead", {}, head), h("tbody", {}, ...rows));
    }
}

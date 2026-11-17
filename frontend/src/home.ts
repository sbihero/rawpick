// 首页：出门目录列表，支持搜索、年份和状态筛选、排序、卡片 / 列表两种视图。
// 文件夹名按 yyyymmdd_主题 解析出日期和主题；不符合格式的照常显示，排在最后。

import { api, type Session } from "./api.js";
import { fmtTime, h, toast } from "./util.js";

type SortKey = "date_desc" | "date_asc" | "topic" | "count";
type ViewKey = "cards" | "list";
type StatusFilter = "all" | Session["status"];

const SORT_LABEL: Record<SortKey, string> = {
  date_desc: "日期：新→旧", date_asc: "日期：旧→新", topic: "主题：A→Z", count: "照片张数：多→少",
};
const STATUS_LABEL: Record<Session["status"], string> = { new: "未打开", opened: "筛选中", exported: "已导出" };
const PREF_KEY = "rawpick.home.v1";

interface Item {
  s: Session;
  date: string | null; // YYYY-MM-DD
  year: string | null;
  topic: string;
}

export function parseName(name: string): { date: string | null; year: string | null; topic: string } {
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

// 排序和视图按设备记住；搜索和筛选只在本次打开期间保留
interface Prefs { sort: SortKey; view: ViewKey }
function loadPrefs(): Prefs {
  const def: Prefs = { sort: "date_desc", view: "cards" };
  try {
    const p = JSON.parse(localStorage.getItem(PREF_KEY) || "{}");
    return {
      sort: p.sort in SORT_LABEL ? p.sort : def.sort,
      view: p.view === "list" || p.view === "cards" ? p.view : def.view,
    };
  } catch {
    return def;
  }
}
function savePrefs(p: Prefs): void {
  try {
    localStorage.setItem(PREF_KEY, JSON.stringify(p));
  } catch {
    /* 隐私模式等情况下存不了，忽略 */
  }
}

const filters = { q: "", year: "", status: "all" as StatusFilter };
const collator = new Intl.Collator(["zh-Hans-CN", "en"], { numeric: true, sensitivity: "base" });

export class HomeView {
  private prefs = loadPrefs();
  private items: Item[] = [];
  private root_path = "";
  private loading = 0;
  private cleanups: (() => void)[] = [];
  private listEl = h("div", { class: "home-list" });
  private summaryEl = h("div", { class: "home-summary muted small" });
  private noticeEl = h("div", { class: "notice", hidden: true });
  private yearSel = h("select", { class: "select", "aria-label": "年份" }) as HTMLSelectElement;
  private refreshBtn = h("button", { class: "btn", text: "刷新" }) as HTMLButtonElement;

  constructor(private root: HTMLElement) {
    document.title = "rawpick";
    this.build();
    void this.load();
    // 标签页回到前台时自动刷新（比如刚在 Windows 上拷完照片）
    const onVis = () => {
      if (document.visibilityState === "visible") void this.load();
    };
    document.addEventListener("visibilitychange", onVis);
    this.cleanups.push(() => document.removeEventListener("visibilitychange", onVis));
  }

  destroy(): void {
    for (const fn of this.cleanups) fn();
  }

  private build(): void {
    const search = h("input", {
      class: "input", type: "search", placeholder: "搜索主题或文件夹名", value: filters.q,
      "aria-label": "搜索",
    }) as HTMLInputElement;
    search.addEventListener("input", () => { filters.q = search.value; this.renderList(); });

    this.yearSel.addEventListener("change", () => { filters.year = this.yearSel.value; this.renderList(); });

    const statusSeg = h("div", { class: "seg", role: "group", "aria-label": "状态" });
    for (const st of ["all", "new", "opened", "exported"] as StatusFilter[]) {
      const b = h("button", { class: "seg-btn", "data-status": st,
        text: st === "all" ? "全部" : STATUS_LABEL[st] });
      b.addEventListener("click", () => { filters.status = st; this.renderList(); });
      statusSeg.append(b);
    }

    const sortSel = h("select", { class: "select", "aria-label": "排序" }) as HTMLSelectElement;
    for (const [k, label] of Object.entries(SORT_LABEL)) {
      sortSel.append(h("option", { value: k, text: label, selected: k === this.prefs.sort }));
    }
    sortSel.addEventListener("change", () => {
      this.prefs.sort = sortSel.value as SortKey;
      savePrefs(this.prefs);
      this.renderList();
    });

    const viewSeg = h("div", { class: "seg", role: "group", "aria-label": "视图" });
    for (const [v, label] of [["cards", "卡片"], ["list", "列表"]] as [ViewKey, string][]) {
      const b = h("button", { class: "seg-btn", "data-view": v, text: label });
      b.addEventListener("click", () => { this.prefs.view = v; savePrefs(this.prefs); this.renderList(); });
      viewSeg.append(b);
    }

    this.refreshBtn.addEventListener("click", () => void this.load(true));

    this.root.replaceChildren(
      h("header", { class: "topbar" },
        h("div", { class: "brand", text: "rawpick" }),
        h("div", { class: "spacer" }),
        this.refreshBtn,
        h("a", { class: "btn", href: "#/settings", text: "⚙ 设置", "aria-label": "设置" })),
      h("main", { class: "home" },
        this.noticeEl,
        h("div", { class: "home-tools" }, search, this.yearSel, statusSeg, sortSel, viewSeg),
        this.summaryEl,
        this.listEl));
  }

  private async load(manual = false): Promise<void> {
    const ticket = ++this.loading;
    if (manual) {
      this.refreshBtn.disabled = true;
      this.refreshBtn.textContent = "刷新中……";
    }
    if (this.items.length === 0) this.listEl.replaceChildren(h("p", { class: "muted", text: "正在读取照片目录……" }));
    try {
      const data = await api.sessions();
      if (ticket !== this.loading) return; // 已有更新的请求
      this.root_path = data.root;
      this.items = data.sessions.map((s) => ({ s, ...parseName(s.name) }));
      for (const a of data.adopted ?? []) toast(`已自动关联标记：${a.from} → ${a.to}`, 5000);
      this.renderNotice(data.unmatched ?? 0);
      this.renderYears();
      this.renderList();
    } catch (e) {
      if (ticket !== this.loading) return;
      this.listEl.replaceChildren(h("p", { class: "error", text: `读取失败：${(e as Error).message}` }));
    } finally {
      if (manual) {
        this.refreshBtn.disabled = false;
        this.refreshBtn.textContent = "刷新";
      }
    }
  }

  private renderNotice(unmatched: number): void {
    this.noticeEl.hidden = unmatched === 0;
    if (unmatched > 0) {
      this.noticeEl.replaceChildren(
        `有 ${unmatched} 个文件夹的标记找不到对应的文件夹（可能改名或移动过）。`,
        h("a", { href: "#/settings", text: "去处理" }));
    }
  }

  private renderYears(): void {
    const years = [...new Set(this.items.map((i) => i.year).filter((y): y is string => !!y))]
      .sort().reverse();
    if (filters.year && !years.includes(filters.year)) filters.year = "";
    this.yearSel.replaceChildren(
      h("option", { value: "", text: "全部年份" }),
      ...years.map((y) => h("option", { value: y, text: `${y} 年`, selected: y === filters.year })),
    );
  }

  private visibleItems(): Item[] {
    const q = filters.q.trim().toLowerCase();
    const out = this.items.filter((i) =>
      (!q || i.s.name.toLowerCase().includes(q) || i.topic.toLowerCase().includes(q))
      && (!filters.year || i.year === filters.year)
      && (filters.status === "all" || i.s.status === filters.status));
    const byName = (a: Item, b: Item) => collator.compare(b.s.name, a.s.name);
    const nullsLast = (a: Item, b: Item) => (a.date === null ? 1 : 0) - (b.date === null ? 1 : 0);
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

  private statusBadge(s: Session): HTMLElement {
    const text = s.status === "exported" ? `已导出 ${s.select_count}` : STATUS_LABEL[s.status];
    return h("span", { class: `status status-${s.status}`, text });
  }

  private countText(s: Session): string {
    return s.scanned ? `${s.total} 张` : `约 ${s.total} 张`;
  }

  private renderList(): void {
    this.root.querySelectorAll<HTMLElement>("[data-status]").forEach((b) =>
      b.classList.toggle("active", b.dataset.status === filters.status));
    this.root.querySelectorAll<HTMLElement>("[data-view]").forEach((b) =>
      b.classList.toggle("active", b.dataset.view === this.prefs.view));

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

  private cards(items: Item[]): HTMLElement {
    const box = h("div", { class: "sessions" });
    for (const { s, date, topic } of items) {
      box.append(h("a", { class: "session-card", href: `#/s/${s.id}`, "data-name": s.name },
        h("div", { class: "session-date muted small", text: date ?? "未识别日期" }),
        h("div", { class: "session-name", text: topic }),
        h("div", { class: "session-meta" },
          h("span", { text: `${this.countText(s)} · 已选 ${s.picked}` }),
          this.statusBadge(s)),
        s.last_export_at
          ? h("div", { class: "session-meta muted small", text: `上次导出 ${fmtTime(s.last_export_at)}` })
          : null));
    }
    return box;
  }

  private table(items: Item[]): HTMLElement {
    const head = h("tr", {}, ...["日期", "主题", "张数", "已选", "状态", "上次导出"].map((t) => h("th", { text: t })));
    const rows = items.map(({ s, date, topic }) => {
      const tr = h("tr", { class: "row-link", "data-name": s.name },
        h("td", { class: "nowrap", text: date ?? "—" }),
        h("td", {}, h("a", { href: `#/s/${s.id}`, text: topic })),
        h("td", { class: "num", text: this.countText(s) }),
        h("td", { class: "num", text: String(s.picked) }),
        h("td", {}, this.statusBadge(s)),
        h("td", { class: "nowrap muted", text: s.last_export_at ? fmtTime(s.last_export_at) : "" }));
      tr.addEventListener("click", (e) => {
        if (!(e.target as HTMLElement).closest("a")) location.hash = `#/s/${s.id}`;
      });
      return tr;
    });
    return h("table", { class: "session-table" }, h("thead", {}, head), h("tbody", {}, ...rows));
  }
}

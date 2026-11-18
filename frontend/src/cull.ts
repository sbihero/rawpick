// 筛选界面：顶栏（过滤、视图、导出、发布相册）+ 共用的照片浏览组件。

import { api, imgUrl, type Issue, type Photo, type Session } from "./api.js";
import { PhotoBrowser, type BItem, type Filter, type Mode } from "./browser.js";
import { openExportPanel } from "./exportPanel.js";
import { showHelp } from "./help.js";
import { openPublishDialog } from "./publish.js";
import { h, nameList, openModal } from "./util.js";

type CItem = Photo & BItem;

const FILTER_LABEL: Record<Filter, string> = { all: "全部", picked: "已选", unpicked: "未选" };
const ISSUE_LABEL: Record<Issue["kind"], string> = {
  raw_only: "只有 RAW、没有 JPG（不参与筛选）",
  conflict: "同一文件名有多个文件（未参与配对）",
  subdir: "被忽略的子目录",
};

export class CullView {
  private session: Session | null = null;
  private modalOpen = false;
  private issues: Issue[] = [];
  private exifPending = new Set<number>();
  private cleanups: (() => void)[] = [];
  private pollTimer = 0;
  private cursorTimer = 0;
  private pendingCursor: number | null = null;
  private browser: PhotoBrowser<CItem>;

  private titleEl = h("div", { class: "title" });
  private progressEl = h("span", { class: "progress muted" });
  private issuesBtn = h("button", { class: "btn small warn-btn", hidden: true });
  private countEl = h("span", { class: "count", "data-testid": "count" });
  /** 次要按钮：宽屏时排在顶栏里，手机上收进“⋯”菜单 */
  private extraEl = h("div", { class: "topbar-extra", "data-testid": "topbar-extra" });
  private moreBtn = h("button", { class: "btn more-btn", text: "⋯", title: "更多", "aria-label": "更多",
    "aria-expanded": "false", "data-testid": "more-btn" });
  private filterBtns = {} as Record<Filter, HTMLButtonElement>;
  private modeBtns = {} as Record<Mode, HTMLButtonElement>;

  constructor(private root: HTMLElement, private sid: number) {
    const body = this.build();
    this.browser = new PhotoBrowser<CItem>(body, {
      text: {
        pick: "选中", picked: "✓ 已选", pickedBadge: "已选", prev: "上一张", next: "下一张", zoom: "放大",
        first: "已经是第一张", last: "已经是最后一张", empty: "这个文件夹里没有照片",
        emptyFilter: (f) => `没有${FILTER_LABEL[f]}的照片`, loadingOriginal: "加载原图……",
        zoomHint: "100% · 再按 Z 或 Esc 退出", outOfFilter: "已不在当前过滤中", checkMark: "✓",
      },
      autoAdvance: true,
      showPick: true,
      canPick: () => true,
      save: (p, picked) => api.setPicked(p.id, picked).catch((e: Error) => {
        throw new Error(`保存失败：${e.message}`);
      }),
      describe: (p) => this.describe(p),
      onRender: () => this.renderHeader(),
      onCurrent: (p) => this.saveCursorSoon(p),
      onAction: (a) => { if (a === "help") this.openHelp(); },
      modalOpen: () => this.modalOpen,
    });
    this.bindEvents();
    void this.load();
  }

  destroy(): void {
    window.clearTimeout(this.pollTimer);
    this.flushCursor();
    this.browser.destroy();
    for (const fn of this.cleanups) fn();
  }

  // ------------------------------------------------------------------ 构建
  private build(): HTMLElement {
    const filters = h("div", { class: "seg", role: "group", "aria-label": "过滤" });
    for (const f of ["all", "picked", "unpicked"] as Filter[]) {
      const b = h("button", { class: "seg-btn", text: FILTER_LABEL[f], "data-filter": f,
        onclick: () => this.browser.setFilter(f) });
      this.filterBtns[f] = b;
      filters.append(b);
    }
    const modes = h("div", { class: "seg", role: "group", "aria-label": "视图" });
    for (const [m, label] of [["loupe", "单张"], ["grid", "网格"]] as [Mode, string][]) {
      const b = h("button", { class: "seg-btn", text: label, onclick: () => this.browser.setMode(m) });
      this.modeBtns[m] = b;
      modes.append(b);
    }
    this.issuesBtn.addEventListener("click", () => this.showIssues());
    this.extraEl.append(
      this.progressEl,
      this.issuesBtn,
      filters,
      modes,
      h("button", { class: "btn help-btn", text: "?", title: "操作说明", "aria-label": "操作说明",
        "data-testid": "help-btn", onclick: () => this.openHelp() }),
      h("button", { class: "btn", text: "发布相册", "data-testid": "publish-btn", onclick: () => this.openPublish() }));
    this.moreBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      this.setMenu(!this.extraEl.classList.contains("open"));
    });
    // 菜单里点了按钮就收起
    this.extraEl.addEventListener("click", (e) => {
      if ((e.target as HTMLElement).closest("button")) this.setMenu(false);
    });
    const header = h("header", { class: "topbar has-more" },
      h("a", { class: "btn back", href: "#/", text: "‹ 返回" }),
      this.titleEl,
      h("div", { class: "spacer" }),
      this.countEl,
      this.extraEl,
      this.moreBtn,
      h("button", { class: "btn primary", text: "导出", onclick: () => this.openExport() }));
    const body = h("div", { class: "body" });
    this.root.replaceChildren(header, body);
    this.root.classList.add("cull-root");
    this.cleanups.push(() => this.root.classList.remove("cull-root"));
    this.titleEl.textContent = "正在扫描……";
    return body;
  }

  private setMenu(open: boolean): void {
    this.extraEl.classList.toggle("open", open);
    this.moreBtn.setAttribute("aria-expanded", String(open));
  }

  private bindEvents(): void {
    // 点菜单以外的地方、按 Esc 时收起“⋯”菜单
    const onDocClick = (e: MouseEvent) => {
      if (this.extraEl.classList.contains("open") && !this.extraEl.contains(e.target as Node)) this.setMenu(false);
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape" && this.extraEl.classList.contains("open")) {
        this.setMenu(false);
        e.stopPropagation();
      }
    };
    document.addEventListener("click", onDocClick);
    document.addEventListener("keydown", onEsc, true);
    this.cleanups.push(() => {
      document.removeEventListener("click", onDocClick);
      document.removeEventListener("keydown", onEsc, true);
    });
    const onHide = () => this.flushCursor();
    const onVis = () => { if (document.visibilityState === "hidden") this.flushCursor(); };
    window.addEventListener("pagehide", onHide);
    document.addEventListener("visibilitychange", onVis);
    this.cleanups.push(() => {
      window.removeEventListener("pagehide", onHide);
      document.removeEventListener("visibilitychange", onVis);
    });
  }

  // ------------------------------------------------------------------ 加载
  private async load(): Promise<void> {
    let photos: Photo[];
    try {
      await api.open(this.sid);
      const data = await api.photos(this.sid);
      this.session = data.session;
      photos = data.photos;
    } catch (e) {
      this.titleEl.textContent = "";
      this.browser.setLoading(`打开失败：${(e as Error).message}`);
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
      this.moreBtn.classList.toggle("alert", r.issues.length > 0);
    }, () => {});
  }

  private async pollStatus(): Promise<void> {
    try {
      const st = await api.status(this.sid);
      const { total, ready, failed } = st.preview;
      if (ready + failed < total) {
        this.progressEl.textContent = `生成预览 ${ready}/${total}`;
        this.pollTimer = window.setTimeout(() => this.pollStatus(), 1000);
      } else {
        this.progressEl.textContent = failed ? `${failed} 张预览失败` : "";
      }
    } catch {
      this.pollTimer = window.setTimeout(() => this.pollStatus(), 5000);
    }
  }

  // ------------------------------------------------------------------ 渲染
  private renderHeader(): void {
    const b = this.browser;
    if (!b) return;
    this.countEl.textContent = `已选 ${b.pickedCount()} / 共 ${b.items.length}`;
    for (const f of Object.keys(this.filterBtns) as Filter[]) {
      this.filterBtns[f].classList.toggle("active", f === b.filter);
    }
    for (const m of Object.keys(this.modeBtns) as Mode[]) {
      this.modeBtns[m].classList.toggle("active", m === b.mode);
    }
  }

  private describe(p: CItem) {
    const badges: HTMLElement[] = [];
    if (!p.raw) badges.push(h("span", { class: "badge noraw", text: "缺 RAW" }));
    if (p.error) badges.push(h("span", { class: "badge noraw", text: "预览失败" }));
    let exif = "";
    if (p.exif) {
      const e = p.exif;
      exif = [e.shutter, e.aperture, e.iso, e.focal].filter(Boolean).join(" · ");
    } else {
      this.fetchExif(p);
    }
    return { badges, exif, cell: { noraw: !p.raw } };
  }

  private fetchExif(p: CItem): void {
    if (this.exifPending.has(p.id)) return;
    this.exifPending.add(p.id);
    api.photo(p.id).then((full) => {
      p.exif = full.exif || {};
      if (this.browser.current() === p) this.browser.refreshCurrent();
    }, () => {}).finally(() => this.exifPending.delete(p.id));
  }

  // ------------------------------------------------------------------ 进度
  private saveCursorSoon(p: CItem | undefined): void {
    window.clearTimeout(this.cursorTimer);
    if (!p) return;
    this.pendingCursor = p.id;
    this.cursorTimer = window.setTimeout(() => this.flushCursor(), 800);
  }

  /** 立即保存还没发出去的位置（切换页面、刷新、关闭标签页时调用）。 */
  private flushCursor(): void {
    window.clearTimeout(this.cursorTimer);
    if (this.pendingCursor === null) return;
    const id = this.pendingCursor;
    this.pendingCursor = null;
    api.cursor(this.sid, id, true).catch(() => {});
  }

  // ------------------------------------------------------------------ 弹窗
  private afterModal(): void {
    this.modalOpen = false;
  }

  private openExport(): void {
    this.modalOpen = true;
    const target = {
      title: "导出到 select/",
      dest: "select",
      plan: () => api.plan(this.sid),
      start: () => api.startExport(this.sid),
    };
    void api.status(this.sid).then(
      (st) => openExportPanel(target, st.export_job, () => this.afterModal()),
      () => openExportPanel(target, null, () => this.afterModal()),
    );
  }

  private openPublish(): void {
    if (!this.session) return;
    this.modalOpen = true;
    openPublishDialog(this.session, this.browser.pickedCount(), () => this.afterModal());
  }

  private openHelp(): void {
    this.modalOpen = true;
    showHelp(() => this.afterModal());
  }

  private showIssues(): void {
    this.modalOpen = true;
    const m = openModal("扫描发现的异常", () => this.afterModal());
    const groups = new Map<Issue["kind"], string[]>();
    for (const i of this.issues) {
      if (!groups.has(i.kind)) groups.set(i.kind, []);
      groups.get(i.kind)!.push(i.name);
    }
    for (const [kind, names] of groups) {
      m.body.append(h("h3", { text: `${ISSUE_LABEL[kind]}（${names.length}）` }), nameList(names));
    }
    m.actions.append(h("button", { class: "btn", text: "关闭", onclick: () => m.close() }));
  }
}

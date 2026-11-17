// 筛选界面：单张 / 网格、标记、过滤、键盘与触屏、预加载、缩放。

import { api, imgUrl, type Issue, type Photo, type Session } from "./api.js";
import { openExportPanel } from "./exportPanel.js";
import { showHelp } from "./help.js";
import { actionFor } from "./keymap.js";
import { Preloader } from "./preload.js";
import { h, nameList, openModal, toast } from "./util.js";

type Filter = "all" | "picked" | "unpicked";
type Mode = "loupe" | "grid";

const FILTER_LABEL: Record<Filter, string> = { all: "全部", picked: "已选", unpicked: "未选" };
const ISSUE_LABEL: Record<Issue["kind"], string> = {
  raw_only: "只有 RAW、没有 JPG（不参与筛选）",
  conflict: "同一文件名有多个文件（未参与配对）",
  subdir: "被忽略的子目录",
};

function matches(p: Photo, f: Filter): boolean {
  return f === "all" || (f === "picked" ? p.picked : !p.picked);
}

export class CullView {
  private session: Session | null = null;
  private photos: Photo[] = [];
  private list: Photo[] = [];
  private filter: Filter = "all";
  private mode: Mode = "loupe";
  private index = 0;
  private zoomed = false;
  private modalOpen = false;
  private issues: Issue[] = [];
  private preloader = new Preloader();
  private exifPending = new Set<number>();
  private cleanups: (() => void)[] = [];
  private pollTimer = 0;
  private cursorTimer = 0;
  private gridBuilt = false;

  // 元素
  private titleEl = h("div", { class: "title" });
  private progressEl = h("span", { class: "progress muted" });
  private issuesBtn = h("button", { class: "btn small warn-btn", hidden: true });
  private countEl = h("span", { class: "count", "data-testid": "count" });
  private filterBtns = {} as Record<Filter, HTMLButtonElement>;
  private modeBtns = {} as Record<Mode, HTMLButtonElement>;
  private stage = h("div", { class: "stage" });
  private mainImg = h("img", { class: "main", alt: "", draggable: "false" });
  private zoomWrap = h("div", { class: "zoomwrap", hidden: true });
  private origImg = h("img", { class: "orig", alt: "", draggable: "false" });
  private zoomNote = h("div", { class: "zoom-note", hidden: true });
  private emptyEl = h("div", { class: "empty", hidden: true });
  private nameEl = h("span", { class: "name", "data-testid": "name" });
  private posEl = h("span", { class: "pos muted", "data-testid": "pos" });
  private exifEl = h("span", { class: "exif muted" });
  private badgesEl = h("span", { class: "badges" });
  private pickBtn = h("button", { class: "pick-btn", "data-testid": "pick-btn" });
  private loupeEl = h("section", { class: "loupe" });
  private gridEl = h("section", { class: "grid", hidden: true });

  constructor(private root: HTMLElement, private sid: number) {
    this.build();
    this.bindEvents();
    void this.load();
  }

  destroy(): void {
    window.clearTimeout(this.pollTimer);
    this.flushCursor();
    this.preloader.clear();
    for (const fn of this.cleanups) fn();
  }

  // ------------------------------------------------------------------ 构建
  private build(): void {
    const filters = h("div", { class: "seg", role: "group", "aria-label": "过滤" });
    for (const f of ["all", "picked", "unpicked"] as Filter[]) {
      const b = h("button", { class: "seg-btn", text: FILTER_LABEL[f], "data-filter": f,
        onclick: () => this.setFilter(f) });
      this.filterBtns[f] = b;
      filters.append(b);
    }
    const modes = h("div", { class: "seg", role: "group", "aria-label": "视图" });
    for (const [m, label] of [["loupe", "单张"], ["grid", "网格"]] as [Mode, string][]) {
      const b = h("button", { class: "seg-btn", text: label, onclick: () => this.setMode(m) });
      this.modeBtns[m] = b;
      modes.append(b);
    }
    this.issuesBtn.addEventListener("click", () => this.showIssues());
    const header = h("header", { class: "topbar" },
      h("a", { class: "btn back", href: "#/", text: "‹ 返回" }),
      this.titleEl,
      h("div", { class: "spacer" }),
      this.progressEl,
      this.issuesBtn,
      this.countEl,
      filters,
      modes,
      h("button", { class: "btn help-btn", text: "?", title: "操作说明", "aria-label": "操作说明",
        "data-testid": "help-btn", onclick: () => this.openHelp() }),
      h("button", { class: "btn primary", text: "导出", onclick: () => this.openExport() }));

    this.zoomWrap.append(this.origImg);
    this.stage.append(this.mainImg, this.zoomWrap, this.zoomNote, this.emptyEl);
    const info = h("div", { class: "infobar" },
      h("div", { class: "info-left" }, this.nameEl, this.posEl, this.badgesEl),
      this.exifEl);
    const controls = h("div", { class: "controls" },
      h("button", { class: "ctl", "aria-label": "上一张", text: "◀", onclick: () => this.move(-1) }),
      this.pickBtn,
      h("button", { class: "ctl", "aria-label": "下一张", text: "▶", onclick: () => this.move(1) }),
      h("button", { class: "ctl zoom-btn", text: "放大", onclick: () => this.toggleZoom() }));
    this.pickBtn.addEventListener("click", () => {
      const p = this.current();
      if (p) this.mark(!p.picked);
    });
    this.loupeEl.append(this.stage, info, controls);

    this.root.replaceChildren(header, h("div", { class: "body" }, this.loupeEl, this.gridEl));
    this.root.classList.add("cull-root");
    this.cleanups.push(() => this.root.classList.remove("cull-root"));
    this.titleEl.textContent = "正在扫描……";
  }

  private on<T extends Event>(target: EventTarget, type: string, fn: (e: T) => void,
    opts?: AddEventListenerOptions): void {
    target.addEventListener(type, fn as EventListener, opts);
    this.cleanups.push(() => target.removeEventListener(type, fn as EventListener, opts));
  }

  // ------------------------------------------------------------------ 加载
  private async load(): Promise<void> {
    try {
      await api.open(this.sid);
      const data = await api.photos(this.sid);
      this.session = data.session;
      this.photos = data.photos;
    } catch (e) {
      this.titleEl.textContent = "";
      this.emptyEl.hidden = false;
      this.emptyEl.textContent = `打开失败：${(e as Error).message}`;
      return;
    }
    this.titleEl.textContent = this.session.name;
    document.title = `${this.session.name} · rawpick`;
    this.list = this.photos.slice();
    const cursor = this.session.cursor;
    const i = cursor === null ? -1 : this.list.findIndex((p) => p.id === cursor);
    this.index = Math.max(0, i);
    this.render();
    void this.pollStatus();
    api.issues(this.sid).then((r) => {
      this.issues = r.issues;
      this.issuesBtn.hidden = r.issues.length === 0;
      this.issuesBtn.textContent = `异常 ${r.issues.length}`;
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

  // ------------------------------------------------------------------ 状态
  private current(): Photo | undefined {
    return this.list[this.index];
  }

  private isOut(p: Photo): boolean {
    return !matches(p, this.filter);
  }

  private pickedCount(): number {
    let n = 0;
    for (const p of this.photos) if (p.picked) n++;
    return n;
  }

  private setFilter(f: Filter): void {
    const cur = this.current();
    const curPos = cur ? this.photos.indexOf(cur) : 0;
    this.filter = f;
    this.list = this.photos.filter((p) => matches(p, f));
    // 尽量停在原来那张；它不在新列表里就停在它后面最近的一张
    let i = cur ? this.list.indexOf(cur) : -1;
    if (i < 0) {
      i = this.list.findIndex((p) => this.photos.indexOf(p) >= curPos);
      if (i < 0) i = this.list.length - 1;
    }
    this.index = Math.max(0, i);
    this.exitZoom();
    this.gridBuilt = false;
    this.render();
  }

  private setMode(m: Mode): void {
    if (this.mode === m) return;
    this.mode = m;
    this.exitZoom();
    this.render();
    if (m === "grid") this.focusCell(true);
  }

  private move(delta: number): void {
    if (this.list.length === 0) return;
    const next = Math.min(this.list.length - 1, Math.max(0, this.index + delta));
    if (next === this.index) {
      if (delta > 0) toast("已经是最后一张");
      else if (delta < 0) toast("已经是第一张");
      return;
    }
    const prev = this.index;
    this.index = next;
    this.exitZoom();
    if (this.mode === "grid") this.updateCell(prev);
    this.render();
  }

  private mark(picked: boolean): void {
    const p = this.current();
    if (!p) return;
    if (p.picked !== picked) {
      p.picked = picked;
      const i = this.index;
      api.setPicked(p.id, picked).catch((e: Error) => {
        p.picked = !picked;
        toast(`保存失败：${e.message}`, 4000);
        this.updateCell(this.list.indexOf(p));
        this.render();
      });
      this.updateCell(i);
    }
    // 自动跳到下一张
    if (this.index < this.list.length - 1) {
      this.move(1);
    } else {
      this.render();
      toast("已经是最后一张");
    }
  }

  // ------------------------------------------------------------------ 渲染
  private render(): void {
    const picked = this.pickedCount();
    this.countEl.textContent = `已选 ${picked} / 共 ${this.photos.length}`;
    for (const f of Object.keys(this.filterBtns) as Filter[]) {
      this.filterBtns[f].classList.toggle("active", f === this.filter);
    }
    for (const m of Object.keys(this.modeBtns) as Mode[]) {
      this.modeBtns[m].classList.toggle("active", m === this.mode);
    }
    this.loupeEl.hidden = this.mode !== "loupe";
    this.gridEl.hidden = this.mode !== "grid";
    if (this.mode === "loupe") this.renderLoupe();
    else this.renderGrid();
    this.saveCursorSoon();
  }

  private renderLoupe(): void {
    const p = this.current();
    if (!p) {
      this.mainImg.hidden = true;
      this.emptyEl.hidden = false;
      this.emptyEl.textContent = this.photos.length === 0
        ? "这个文件夹里没有照片"
        : `没有${FILTER_LABEL[this.filter]}的照片`;
      this.nameEl.textContent = "";
      this.posEl.textContent = "";
      this.exifEl.textContent = "";
      this.badgesEl.replaceChildren();
      this.pickBtn.disabled = true;
      this.preloader.clear();
      return;
    }
    this.emptyEl.hidden = true;
    this.mainImg.hidden = false;
    this.pickBtn.disabled = false;

    // 预加载窗口：当前、后两张、前一张
    const win = [this.index, this.index + 1, this.index + 2, this.index - 1]
      .filter((i) => i >= 0 && i < this.list.length)
      .map((i) => this.list[i]);
    this.preloader.keep(win);
    const entry = this.preloader.ensure(p);
    const previewUrl = imgUrl(p, "preview");
    if (entry.decoded) {
      this.mainImg.src = previewUrl;
    } else {
      // 先用缩略图顶上，大预览解码好后再换
      this.mainImg.src = imgUrl(p, "thumb");
      void entry.ready.then(() => {
        if (this.current() === p && this.mode === "loupe") this.mainImg.src = previewUrl;
      });
    }

    this.nameEl.textContent = p.name;
    this.posEl.textContent = `${this.index + 1} / ${this.list.length}`;
    const badges: HTMLElement[] = [];
    if (p.picked) badges.push(h("span", { class: "badge picked", text: "已选" }));
    if (!p.raw) badges.push(h("span", { class: "badge noraw", text: "缺 RAW" }));
    if (this.isOut(p)) badges.push(h("span", { class: "badge out", text: "已不在当前过滤中" }));
    if (p.error) badges.push(h("span", { class: "badge noraw", text: "预览失败" }));
    this.badgesEl.replaceChildren(...badges);
    this.stage.classList.toggle("out", this.isOut(p));
    this.pickBtn.textContent = p.picked ? "✓ 已选" : "选中";
    this.pickBtn.classList.toggle("on", p.picked);

    if (p.exif) this.exifEl.textContent = this.exifText(p);
    else {
      this.exifEl.textContent = "";
      this.fetchExif(p);
    }
  }

  private exifText(p: Photo): string {
    const e = p.exif || {};
    return [e.shutter, e.aperture, e.iso, e.focal].filter(Boolean).join(" · ");
  }

  private fetchExif(p: Photo): void {
    if (this.exifPending.has(p.id)) return;
    this.exifPending.add(p.id);
    api.photo(p.id).then((full) => {
      p.exif = full.exif || {};
      if (this.current() === p && this.mode === "loupe") this.exifEl.textContent = this.exifText(p);
    }, () => {}).finally(() => this.exifPending.delete(p.id));
  }

  private pendingCursor: number | null = null;

  private saveCursorSoon(): void {
    window.clearTimeout(this.cursorTimer);
    const p = this.current();
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

  // ------------------------------------------------------------------ 网格
  private renderGrid(): void {
    if (!this.gridBuilt) {
      const frag = document.createDocumentFragment();
      this.list.forEach((p, i) => {
        const cell = h("div", { class: "cell", "data-i": i },
          h("img", { src: imgUrl(p, "thumb"), loading: "lazy", alt: "", draggable: "false" }),
          h("span", { class: "check", text: "✓" }),
          h("span", { class: "cap", text: p.name }));
        frag.append(cell);
      });
      this.gridEl.replaceChildren(frag);
      if (this.list.length === 0) {
        this.gridEl.append(h("div", { class: "empty", text: `没有${FILTER_LABEL[this.filter]}的照片` }));
      }
      this.gridBuilt = true;
      this.list.forEach((_, i) => this.updateCell(i));
    }
    this.focusCell(false);
  }

  private cellAt(i: number): HTMLElement | null {
    return this.gridEl.children[i] as HTMLElement | null;
  }

  private updateCell(i: number): void {
    const p = this.list[i];
    const cell = this.cellAt(i);
    if (!p || !cell || !this.gridBuilt) return;
    cell.classList.toggle("picked", p.picked);
    cell.classList.toggle("out", this.isOut(p));
    cell.classList.toggle("noraw", !p.raw);
    cell.classList.toggle("focus", i === this.index && this.mode === "grid");
  }

  private focusCell(center: boolean): void {
    this.gridEl.querySelector(".cell.focus")?.classList.remove("focus");
    const cell = this.cellAt(this.index);
    if (!cell) return;
    cell.classList.add("focus");
    cell.scrollIntoView({ block: center ? "center" : "nearest" });
  }

  private gridColumns(): number {
    const first = this.cellAt(0);
    if (!first) return 1;
    const top = first.offsetTop;
    let n = 0;
    for (const c of Array.from(this.gridEl.children) as HTMLElement[]) {
      if (c.offsetTop !== top) break;
      n++;
    }
    return Math.max(1, n);
  }

  // ------------------------------------------------------------------ 缩放
  private toggleZoom(at?: { x: number; y: number }): void {
    if (this.zoomed) this.exitZoom();
    else this.enterZoom(at);
  }

  private enterZoom(at?: { x: number; y: number }): void {
    const p = this.current();
    if (!p || this.mode !== "loupe") return;
    // 点击位置在图片上的相对坐标（object-fit: contain 下要算出实际显示区域）
    let fx = 0.5;
    let fy = 0.5;
    const r = this.mainImg.getBoundingClientRect();
    const nw = this.mainImg.naturalWidth;
    const nh = this.mainImg.naturalHeight;
    if (at && nw && nh) {
      const scale = Math.min(r.width / nw, r.height / nh);
      const dw = nw * scale;
      const dh = nh * scale;
      const left = r.left + (r.width - dw) / 2;
      const top = r.top + (r.height - dh) / 2;
      fx = Math.min(1, Math.max(0, (at.x - left) / dw));
      fy = Math.min(1, Math.max(0, (at.y - top) / dh));
    }
    this.zoomed = true;
    this.stage.classList.add("zoomed");
    this.zoomWrap.hidden = false;
    this.zoomNote.hidden = false;
    this.zoomNote.textContent = "加载原图……";
    const center = () => {
      this.zoomWrap.scrollLeft = fx * this.origImg.naturalWidth - this.zoomWrap.clientWidth / 2;
      this.zoomWrap.scrollTop = fy * this.origImg.naturalHeight - this.zoomWrap.clientHeight / 2;
      this.zoomNote.textContent = "100% · 再按 Z 或 Esc 退出";
    };
    this.origImg.onload = center;
    this.origImg.src = imgUrl(p, "original");
    if (this.origImg.complete && this.origImg.naturalWidth) center();
  }

  private exitZoom(): void {
    if (!this.zoomed) return;
    this.zoomed = false;
    this.stage.classList.remove("zoomed");
    this.zoomWrap.hidden = true;
    this.zoomNote.hidden = true;
    this.origImg.onload = null;
    this.origImg.removeAttribute("src");
  }

  // ------------------------------------------------------------------ 弹窗
  private openExport(): void {
    this.modalOpen = true;
    void api.status(this.sid).then(
      (st) => openExportPanel(this.sid, st.export_job, () => this.afterModal()),
      () => openExportPanel(this.sid, null, () => this.afterModal()),
    );
  }

  private afterModal(): void {
    this.modalOpen = false;
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

  // ------------------------------------------------------------------ 事件
  private bindEvents(): void {
    this.on<KeyboardEvent>(document, "keydown", (e) => this.onKey(e));
    this.on(window, "pagehide", () => this.flushCursor());
    this.on(document, "visibilitychange", () => {
      if (document.visibilityState === "hidden") this.flushCursor();
    });
    this.on<MouseEvent>(this.gridEl, "click", (e) => {
      const cell = (e.target as HTMLElement).closest(".cell") as HTMLElement | null;
      if (!cell) return;
      const prev = this.index;
      this.index = Number(cell.dataset.i);
      this.updateCell(prev);
      this.setMode("loupe");
    });
    // 点完按钮立即失焦，免得之后按键盘时误触发按钮
    this.on<MouseEvent>(this.root, "click", (e) => {
      (e.target as HTMLElement).closest("button")?.blur();
    });
    // Safari：阻止整页捏合缩放
    this.on(document, "gesturestart", (e) => e.preventDefault());
    this.bindMouse();
    this.bindTouch();
  }

  private onKey(e: KeyboardEvent): void {
    if (this.modalOpen) {
      if (e.key === "Escape") {
        document.querySelector<HTMLElement>(".backdrop")?.click();
      }
      return;
    }
    if (e.metaKey || e.ctrlKey || e.altKey || e.isComposing) return;
    // Backspace 一律拦截，避免浏览器把它当成“后退”
    if (e.code === "Backspace") e.preventDefault();
    if (e.key === "Escape") {
      if (this.zoomed) {
        this.exitZoom();
        e.preventDefault();
      }
      return;
    }
    const action = actionFor(e, this.mode);
    if (!action) return;
    e.preventDefault();
    switch (action) {
      case "prev": this.move(-1); break;
      case "next": this.move(1); break;
      case "up": this.move(-this.gridColumns()); break;
      case "down": this.move(this.gridColumns()); break;
      case "pick": this.mark(true); break;
      case "unpick": this.mark(false); break;
      case "zoom": this.toggleZoom(); break;
      case "grid": this.setMode("grid"); break;
      case "loupe": this.setMode("loupe"); break;
      case "help": this.openHelp(); break;
    }
  }

  private bindMouse(): void {
    // 单击图片放大到点击位置；放大后拖动平移，单击（不拖动）退出
    let drag: { x: number; y: number; sl: number; st: number; moved: boolean } | null = null;
    this.on<MouseEvent>(this.stage, "mousedown", (e) => {
      if (e.button !== 0) return;
      if (!this.zoomed) return;
      drag = { x: e.clientX, y: e.clientY, sl: this.zoomWrap.scrollLeft, st: this.zoomWrap.scrollTop, moved: false };
      e.preventDefault();
    });
    this.on<MouseEvent>(window, "mousemove", (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.x;
      const dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
      this.zoomWrap.scrollLeft = drag.sl - dx;
      this.zoomWrap.scrollTop = drag.st - dy;
    });
    this.on<MouseEvent>(window, "mouseup", () => {
      if (drag && !drag.moved) this.exitZoom();
      drag = null;
    });
    this.on<MouseEvent>(this.mainImg, "click", (e) => {
      if (!this.zoomed && !this.touchActive) this.enterZoom({ x: e.clientX, y: e.clientY });
    });
  }

  private touchActive = false;

  private bindTouch(): void {
    let start: { x: number; y: number; t: number } | null = null;
    let pinchStart = 0;
    let pinchDone = false;
    let lastTap = { t: 0, x: 0, y: 0 };
    const dist = (t: TouchList) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);

    this.on<TouchEvent>(this.stage, "touchstart", (e) => {
      this.touchActive = true;
      if (e.touches.length === 2) {
        pinchStart = dist(e.touches);
        pinchDone = false;
        start = null;
      } else if (e.touches.length === 1) {
        const t = e.touches[0];
        start = { x: t.clientX, y: t.clientY, t: Date.now() };
      }
    }, { passive: true });

    this.on<TouchEvent>(this.stage, "touchmove", (e) => {
      if (e.touches.length === 2 && pinchStart > 0 && !pinchDone) {
        const scale = dist(e.touches) / pinchStart;
        const mid = {
          x: (e.touches[0].clientX + e.touches[1].clientX) / 2,
          y: (e.touches[0].clientY + e.touches[1].clientY) / 2,
        };
        if (!this.zoomed && scale > 1.25) {
          this.enterZoom(mid);
          pinchDone = true;
        } else if (this.zoomed && scale < 0.8) {
          this.exitZoom();
          pinchDone = true;
        }
        e.preventDefault();
      } else if (!this.zoomed) {
        e.preventDefault(); // 未放大时自己处理滑动，不让页面跟着动
      }
    }, { passive: false });

    this.on<TouchEvent>(this.stage, "touchend", (e) => {
      if (e.touches.length > 0) return;
      window.setTimeout(() => { this.touchActive = false; }, 400);
      if (pinchStart > 0) {
        pinchStart = 0;
        return;
      }
      if (!start) return;
      const t = e.changedTouches[0];
      const dx = t.clientX - start.x;
      const dy = t.clientY - start.y;
      const dt = Date.now() - start.t;
      start = null;
      if (!this.zoomed && Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5 && dt < 800) {
        this.move(dx < 0 ? 1 : -1);
        return;
      }
      if (Math.abs(dx) < 10 && Math.abs(dy) < 10) {
        const now = Date.now();
        if (now - lastTap.t < 300 && Math.hypot(t.clientX - lastTap.x, t.clientY - lastTap.y) < 30) {
          this.toggleZoom({ x: t.clientX, y: t.clientY });
          lastTap = { t: 0, x: 0, y: 0 };
        } else {
          lastTap = { t: now, x: t.clientX, y: t.clientY };
        }
      }
    });
  }
}

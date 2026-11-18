// 照片浏览组件：单张 / 网格、标记、过滤、预加载、缩放、键盘、鼠标、触屏。
// 管理员的筛选界面和客户的相册页面共用它，各自在外面加顶栏和自己的按钮。

import { actionFor, type ActionId } from "./keymap.js";
import { Preloader } from "./preload.js";
import { h, toast } from "./util.js";

export type Filter = "all" | "picked" | "unpicked";
export type Mode = "loupe" | "grid";

export interface BItem {
  id: number;
  name: string;
  picked: boolean;
  thumb: string;
  preview: string;
  /** 放大时加载的图；null 表示没有原图，直接把大预览按 100% 显示 */
  original: string | null;
}

export interface BrowserText {
  pick: string;
  picked: string;
  pickedBadge: string;
  prev: string;
  next: string;
  zoom: string;
  first: string;
  last: string;
  empty: string;
  emptyFilter: (f: Filter) => string;
  loadingOriginal: string;
  zoomHint: string;
  outOfFilter: string;
  checkMark: string;
}

export interface Description {
  badges?: HTMLElement[];
  exif?: string;
  /** 给网格格子加的 class，比如 noraw、note */
  cell?: Record<string, boolean>;
}

export interface BrowserOptions<T extends BItem> {
  text: BrowserText;
  /** 标记后自动跳到下一张 */
  autoAdvance: boolean;
  /** 是否显示选取按钮（交付成片时不显示） */
  showPick: boolean;
  /** 现在能不能改标记（客户提交或截止后不能） */
  canPick: () => boolean;
  /** 不能改标记时按键或点按钮的提示 */
  lockedMessage?: () => string;
  /** 保存标记；失败时组件会把标记改回去 */
  save: (item: T, picked: boolean) => Promise<unknown>;
  describe?: (item: T) => Description;
  /** 每次渲染后调用（更新顶栏计数、按钮状态） */
  onRender?: () => void;
  /** 当前照片变了（保存进度用） */
  onCurrent?: (item: T | undefined) => void;
  /** 组件自己不处理的按键动作（比如 help） */
  onAction?: (action: ActionId) => void;
  /** 放在底部控制条里的额外按钮 */
  extraControls?: HTMLElement[];
  modalOpen: () => boolean;
  initialMode?: Mode;
  pickClass?: string;
}

function matches(p: BItem, f: Filter): boolean {
  return f === "all" || (f === "picked" ? p.picked : !p.picked);
}

export class PhotoBrowser<T extends BItem> {
  items: T[] = [];
  private list: T[] = [];
  filter: Filter = "all";
  mode: Mode = "loupe";
  private index = 0;
  private zoomed = false;
  private active = true;
  private preloader = new Preloader();
  private cleanups: (() => void)[] = [];
  private gridBuilt = false;
  private touchActive = false;

  readonly stage = h("div", { class: "stage" });
  private mainImg = h("img", { class: "main", alt: "", draggable: "false" });
  private zoomWrap = h("div", { class: "zoomwrap", hidden: true });
  private origImg = h("img", { class: "orig", alt: "", draggable: "false" });
  private zoomNote = h("div", { class: "zoom-note", hidden: true });
  private emptyEl = h("div", { class: "empty", hidden: true });
  private nameEl = h("span", { class: "name", "data-testid": "name" });
  private posEl = h("span", { class: "pos muted", "data-testid": "pos" });
  private exifEl = h("span", { class: "exif muted" });
  private badgesEl = h("span", { class: "badges" });
  readonly pickBtn = h("button", { class: "pick-btn", "data-testid": "pick-btn" });
  private loupeEl = h("section", { class: "loupe" });
  private gridEl = h("section", { class: "grid", hidden: true });

  constructor(private container: HTMLElement, private opts: BrowserOptions<T>) {
    this.mode = opts.initialMode ?? "loupe";
    if (opts.pickClass) this.pickBtn.classList.add(opts.pickClass);
    this.build();
    this.bindEvents();
  }

  destroy(): void {
    this.preloader.clear();
    for (const fn of this.cleanups) fn();
  }

  /** 多个浏览组件同时存在时，只有激活的那个响应键盘。 */
  setActive(on: boolean): void {
    this.active = on;
    if (!on) {
      this.exitZoom();
      this.preloader.clear();
    } else {
      this.render();
    }
  }

  setLoading(message: string): void {
    this.emptyEl.hidden = false;
    this.emptyEl.textContent = message;
    this.mainImg.hidden = true;
  }

  // ------------------------------------------------------------------ 构建
  private build(): void {
    const t = this.opts.text;
    this.zoomWrap.append(this.origImg);
    this.stage.append(this.mainImg, this.zoomWrap, this.zoomNote, this.emptyEl);
    const info = h("div", { class: "infobar" },
      h("div", { class: "info-left" }, this.nameEl, this.posEl, this.badgesEl),
      this.exifEl);
    const controls = h("div", { class: "controls" },
      h("button", { class: "ctl", "aria-label": t.prev, title: t.prev, text: "◀", onclick: () => this.move(-1) }),
      this.opts.showPick ? this.pickBtn : null,
      h("button", { class: "ctl", "aria-label": t.next, title: t.next, text: "▶", onclick: () => this.move(1) }),
      h("button", { class: "ctl zoom-btn", text: t.zoom, onclick: () => this.toggleZoom() }),
      ...(this.opts.extraControls ?? []));
    this.pickBtn.addEventListener("click", () => {
      const p = this.current();
      if (p) this.mark(!p.picked);
    });
    this.loupeEl.append(this.stage, info, controls);
    this.container.append(this.loupeEl, this.gridEl);
  }

  private on<E extends Event>(target: EventTarget, type: string, fn: (e: E) => void,
    opts?: AddEventListenerOptions): void {
    target.addEventListener(type, fn as EventListener, opts);
    this.cleanups.push(() => target.removeEventListener(type, fn as EventListener, opts));
  }

  // ------------------------------------------------------------------ 数据
  setItems(items: T[], startId: number | null = null): void {
    this.items = items;
    this.list = items.filter((p) => matches(p, this.filter));
    const i = startId === null ? -1 : this.list.findIndex((p) => p.id === startId);
    this.index = Math.max(0, i);
    this.gridBuilt = false;
    this.render();
    if (this.mode === "grid") this.focusCell(true);
  }

  current(): T | undefined {
    return this.list[this.index];
  }

  pickedCount(): number {
    let n = 0;
    for (const p of this.items) if (p.picked) n++;
    return n;
  }

  private isOut(p: T): boolean {
    return !matches(p, this.filter);
  }

  setFilter(f: Filter): void {
    const cur = this.current();
    const curPos = cur ? this.items.indexOf(cur) : 0;
    this.filter = f;
    this.list = this.items.filter((p) => matches(p, f));
    // 尽量停在原来那张；它不在新列表里就停在它后面最近的一张
    let i = cur ? this.list.indexOf(cur) : -1;
    if (i < 0) {
      i = this.list.findIndex((p) => this.items.indexOf(p) >= curPos);
      if (i < 0) i = this.list.length - 1;
    }
    this.index = Math.max(0, i);
    this.exitZoom();
    this.gridBuilt = false;
    this.render();
  }

  setMode(m: Mode): void {
    if (this.mode === m) return;
    this.mode = m;
    this.exitZoom();
    this.render();
    if (m === "grid") this.focusCell(true);
  }

  move(delta: number): void {
    if (this.list.length === 0) return;
    const next = Math.min(this.list.length - 1, Math.max(0, this.index + delta));
    if (next === this.index) {
      if (delta > 0) toast(this.opts.text.last);
      else if (delta < 0) toast(this.opts.text.first);
      return;
    }
    const prev = this.index;
    this.index = next;
    this.exitZoom();
    if (this.mode === "grid") this.updateCell(prev);
    this.render();
  }

  /** 设置当前照片的标记；autoAdvance 时随后跳到下一张。 */
  mark(picked: boolean): void {
    const p = this.current();
    if (!p || !this.opts.showPick) return;
    if (!this.opts.canPick()) {
      toast(this.opts.lockedMessage?.() ?? "", 3000);
      return;
    }
    if (p.picked !== picked) {
      p.picked = picked;
      const i = this.index;
      this.opts.save(p, picked).catch((e: Error) => {
        p.picked = !picked;
        toast(e.message, 4000);
        this.updateCell(this.list.indexOf(p));
        this.render();
      });
      this.updateCell(i);
    }
    if (this.opts.autoAdvance) {
      if (this.index < this.list.length - 1) {
        this.move(1);
      } else {
        this.render();
        toast(this.opts.text.last);
      }
    } else {
      this.render();
    }
  }

  // ------------------------------------------------------------------ 渲染
  render(): void {
    this.loupeEl.hidden = this.mode !== "loupe";
    this.gridEl.hidden = this.mode !== "grid";
    if (this.mode === "loupe") this.renderLoupe();
    else this.renderGrid();
    this.opts.onRender?.();
    this.opts.onCurrent?.(this.current());
  }

  /** 外部改了当前照片的信息（备注、EXIF）后刷新信息栏和格子。 */
  refreshCurrent(): void {
    this.updateCell(this.index);
    if (this.mode === "loupe") this.renderInfo();
    this.opts.onRender?.();
  }

  private renderLoupe(): void {
    const p = this.current();
    const t = this.opts.text;
    if (!p) {
      this.mainImg.hidden = true;
      this.emptyEl.hidden = false;
      this.emptyEl.textContent = this.items.length === 0 ? t.empty : t.emptyFilter(this.filter);
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
    if (this.active) this.preloader.keep(win);
    const entry = this.preloader.ensure(p);
    if (entry.decoded) {
      this.mainImg.src = p.preview;
    } else {
      // 先用缩略图顶上，大预览解码好后再换
      this.mainImg.src = p.thumb;
      void entry.ready.then(() => {
        if (this.current() === p && this.mode === "loupe") this.mainImg.src = p.preview;
      });
    }
    this.renderInfo();
  }

  private renderInfo(): void {
    const p = this.current();
    if (!p) return;
    const t = this.opts.text;
    this.nameEl.textContent = p.name;
    this.posEl.textContent = `${this.index + 1} / ${this.list.length}`;
    const d = this.opts.describe?.(p) ?? {};
    const badges: HTMLElement[] = [];
    if (p.picked && this.opts.showPick) badges.push(h("span", { class: "badge picked", text: t.pickedBadge }));
    badges.push(...(d.badges ?? []));
    if (this.isOut(p)) badges.push(h("span", { class: "badge out", text: t.outOfFilter }));
    this.badgesEl.replaceChildren(...badges);
    this.stage.classList.toggle("out", this.isOut(p));
    this.pickBtn.textContent = p.picked ? t.picked : t.pick;
    this.pickBtn.classList.toggle("on", p.picked);
    this.pickBtn.classList.toggle("locked", !this.opts.canPick());
    this.exifEl.textContent = d.exif ?? "";
  }

  // ------------------------------------------------------------------ 网格
  private renderGrid(): void {
    if (!this.gridBuilt) {
      const frag = document.createDocumentFragment();
      this.list.forEach((p, i) => {
        const cell = h("div", { class: "cell", "data-i": i },
          h("img", { src: p.thumb, loading: "lazy", alt: "", draggable: "false" }),
          h("span", { class: "check", text: this.opts.text.checkMark }),
          h("span", { class: "flag", text: "✎" }),
          h("span", { class: "cap", text: p.name }));
        frag.append(cell);
      });
      this.gridEl.replaceChildren(frag);
      if (this.list.length === 0) {
        this.gridEl.append(h("div", { class: "empty",
          text: this.items.length === 0 ? this.opts.text.empty : this.opts.text.emptyFilter(this.filter) }));
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
    cell.classList.toggle("picked", p.picked && this.opts.showPick);
    cell.classList.toggle("out", this.isOut(p));
    cell.classList.toggle("focus", i === this.index && this.mode === "grid");
    const extra = this.opts.describe?.(p).cell ?? {};
    for (const [k, v] of Object.entries(extra)) cell.classList.toggle(k, v);
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
  toggleZoom(at?: { x: number; y: number }): void {
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
    this.zoomNote.textContent = this.opts.text.loadingOriginal;
    const center = () => {
      this.zoomWrap.scrollLeft = fx * this.origImg.naturalWidth - this.zoomWrap.clientWidth / 2;
      this.zoomWrap.scrollTop = fy * this.origImg.naturalHeight - this.zoomWrap.clientHeight / 2;
      this.zoomNote.textContent = this.opts.text.zoomHint;
    };
    this.origImg.onload = center;
    this.origImg.src = p.original ?? p.preview;
    if (this.origImg.complete && this.origImg.naturalWidth) center();
  }

  exitZoom(): void {
    if (!this.zoomed) return;
    this.zoomed = false;
    this.stage.classList.remove("zoomed");
    this.zoomWrap.hidden = true;
    this.zoomNote.hidden = true;
    this.origImg.onload = null;
    this.origImg.removeAttribute("src");
  }

  // ------------------------------------------------------------------ 事件
  private bindEvents(): void {
    this.on<KeyboardEvent>(document, "keydown", (e) => this.onKey(e));
    this.on<MouseEvent>(this.gridEl, "click", (e) => {
      const cell = (e.target as HTMLElement).closest(".cell") as HTMLElement | null;
      if (!cell) return;
      const prev = this.index;
      this.index = Number(cell.dataset.i);
      this.updateCell(prev);
      this.setMode("loupe");
    });
    // 点完按钮立即失焦，免得之后按键盘时误触发按钮
    this.on<MouseEvent>(this.container, "click", (e) => {
      (e.target as HTMLElement).closest("button")?.blur();
    });
    // Safari：阻止整页捏合缩放
    this.on(document, "gesturestart", (e) => e.preventDefault());
    this.bindMouse();
    this.bindTouch();
  }

  private onKey(e: KeyboardEvent): void {
    if (!this.active) return;
    if (this.opts.modalOpen()) {
      if (e.key === "Escape") {
        document.querySelector<HTMLElement>(".backdrop")?.click();
      }
      return;
    }
    const target = e.target as HTMLElement | null;
    if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) return;
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
      default: this.opts.onAction?.(action);
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

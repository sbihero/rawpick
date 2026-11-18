// 客户页面：输入密码 → 选片（选中、备注、提交）→ 下载成片。
// 地址是 /g/<随机串>，所有接口都在这个前缀下面。

import { PhotoBrowser, type BItem, type Filter, type Mode } from "./browser.js";
import { detectLang, fmtDay, helpLines, setLang, t } from "./i18n.js";
import { fmtBytes, h, openModal, toast } from "./util.js";

interface Info {
  lang: "auto" | "zh" | "en";
  studio: string;
  auth_required: boolean;
  title?: string;
  client_name?: string;
  message?: string;
  status?: "building" | "open" | "submitted";
  build?: { ready: number; total: number } | null;
  locked?: "submitted" | "deadline" | null;
  deadline_at?: number | null;
  delete_at?: number | null;
  limit?: number | null;
  limit_mode?: "block" | "warn";
  total?: number;
  selected?: number;
  submitted_at?: number | null;
  delivery?: number;
}

interface CPhoto extends BItem {
  note: string;
}

interface DPhoto extends BItem {
  size: number;
  file: string;
}

class ReqError extends Error {
  constructor(message: string, public status: number, public code: string, public data: Record<string, unknown>) {
    super(message);
  }
}

const token = decodeURIComponent(location.pathname.split("/")[2] || "");
const base = `/g/${encodeURIComponent(token)}`;

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  let r: Response;
  try {
    r = await fetch(base + path, {
      method,
      credentials: "same-origin",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ReqError(t("networkError"), 0, "network", {});
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ReqError(data.error || `HTTP ${r.status}`, r.status, data.code || "", data);
  return data as T;
}

const api = {
  info: () => req<Info>("GET", "/api/info"),
  login: (password: string) => req<{ ok: boolean }>("POST", "/api/login", { password }),
  photos: () => req<{ photos: { id: number; name: string; selected: boolean; note: string }[] }>(
    "GET", "/api/photos"),
  update: (pid: number, patch: { selected?: boolean; note?: string }) =>
    req<{ id: number; selected: boolean; note: string; selected_count: number }>("PUT", `/api/photos/${pid}`, patch),
  submit: (confirm: boolean) => req<{ ok: boolean; selected: number; submitted_at: number }>(
    "POST", "/api/submit", { confirm }),
  delivery: () => req<{ photos: { id: number; name: string; size: number }[] }>("GET", "/api/delivery"),
};

function seenKey(): string {
  return `rawpick.g.${token}.seen`;
}

export class ClientApp {
  private info: Info | null = null;
  private modalOpen = false;
  private view: "proof" | "final" = "proof";
  private proof: PhotoBrowser<CPhoto> | null = null;
  private final: PhotoBrowser<DPhoto> | null = null;
  private finalItems: DPhoto[] = [];

  private countEl = h("span", { class: "count", "data-testid": "count" });
  private noticeEl = h("div", { class: "client-notice", "data-testid": "client-notice" });
  private submitBtn = h("button", { class: "btn primary", "data-testid": "submit-btn" }) as HTMLButtonElement;
  private filterBtns = {} as Record<Filter, HTMLButtonElement>;
  private modeBtns = {} as Record<Mode, HTMLButtonElement>;
  private tabBtns = {} as Record<"proof" | "final", HTMLButtonElement>;
  private filterSeg = h("div", { class: "seg", role: "group" });
  private tabSeg = h("div", { class: "seg", role: "group", hidden: true });
  private zipLink = h("a", { class: "btn primary", hidden: true, "data-testid": "zip-link" });
  private proofBody = h("div", { class: "body" });
  private finalBody = h("div", { class: "body", hidden: true });
  private downloadLink = h("a", { class: "ctl dl-btn", "data-testid": "download-link" });

  constructor(private root: HTMLElement) {
    setLang(detectLang("auto"));
    void this.start();
  }

  // ------------------------------------------------------------------ 启动
  private async start(): Promise<void> {
    this.message(t("loading"));
    let info: Info;
    try {
      info = await api.info();
    } catch (e) {
      const err = e as ReqError;
      this.message(err.code === "not_found" ? t("notFound") : err.code === "deleted" ? t("deleted") : err.message);
      return;
    }
    this.info = info;
    setLang(detectLang(info.lang));
    if (info.auth_required) {
      this.passwordScreen(info);
      return;
    }
    document.title = info.studio ? `${info.title} · ${info.studio}` : info.title || "";
    if (info.status === "building") {
      const b = info.build;
      this.message(b ? t("buildingProgress", b) : t("building"), true);
      return;
    }
    await this.mainScreen();
  }

  private message(text: string, withRefresh = false): void {
    this.root.replaceChildren(h("div", { class: "center-card" },
      this.info?.studio ? h("div", { class: "studio", text: this.info.studio }) : null,
      h("p", { "data-testid": "client-message", text }),
      withRefresh ? h("button", { class: "btn", text: t("refresh"), onclick: () => location.reload() }) : null));
  }

  private passwordScreen(info: Info): void {
    const input = h("input", { class: "input", type: "password", placeholder: t("passwordPlaceholder"),
      autocomplete: "current-password", "data-testid": "password" }) as HTMLInputElement;
    const err = h("p", { class: "error", hidden: true });
    const btn = h("button", { class: "btn primary", type: "submit", text: t("enter") }) as HTMLButtonElement;
    const form = h("form", { class: "center-card" },
      info.studio ? h("div", { class: "studio", text: info.studio }) : null,
      h("h1", { text: t("passwordTitle") }),
      h("p", { class: "muted", text: t("passwordHint") }),
      input, btn, err);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!input.value) return;
      btn.disabled = true;
      err.hidden = true;
      try {
        await api.login(input.value);
        await this.start();
      } catch (ex) {
        const x = ex as ReqError;
        err.textContent = x.code === "bad_password" ? t("badPassword") : x.code === "too_many_attempts" ? t("tooMany") : x.message;
        err.hidden = false;
        btn.disabled = false;
        input.select();
      }
    });
    this.root.replaceChildren(form);
    input.focus();
  }

  // ------------------------------------------------------------------ 主界面
  private async mainScreen(): Promise<void> {
    const info = this.info!;
    let photos;
    try {
      photos = (await api.photos()).photos;
    } catch (e) {
      this.message((e as Error).message);
      return;
    }
    this.buildLayout(info);
    const items: CPhoto[] = photos.map((p) => ({
      id: p.id, name: p.name, picked: p.selected, note: p.note,
      thumb: `${base}/img/${p.id}/thumb`, preview: `${base}/img/${p.id}/preview`, original: null,
    }));
    this.proof!.setItems(items);
    this.renderNotice();
    if (info.delivery) void this.loadDelivery();
    try {
      if (!localStorage.getItem(seenKey())) this.showWelcome();
    } catch {
      this.showWelcome();
    }
  }

  private buildLayout(info: Info): void {
    const filterLabel: Record<Filter, string> = { all: t("all"), picked: t("selected"), unpicked: t("unselected") };
    for (const f of ["all", "picked", "unpicked"] as Filter[]) {
      const b = h("button", { class: "seg-btn", text: filterLabel[f], "data-filter": f,
        onclick: () => this.proof?.setFilter(f) }) as HTMLButtonElement;
      this.filterBtns[f] = b;
      this.filterSeg.append(b);
    }
    const modes = h("div", { class: "seg", role: "group" });
    for (const [m, label] of [["grid", t("grid")], ["loupe", t("loupe")]] as [Mode, string][]) {
      const b = h("button", { class: "seg-btn", text: label, "data-mode": m,
        onclick: () => this.current()?.setMode(m) }) as HTMLButtonElement;
      this.modeBtns[m] = b;
      modes.append(b);
    }
    for (const [v, label] of [["proof", t("tabProof")], ["final", t("tabFinal")]] as ["proof" | "final", string][]) {
      const b = h("button", { class: "seg-btn", text: label, "data-tab": v,
        onclick: () => this.setView(v) }) as HTMLButtonElement;
      this.tabBtns[v] = b;
      this.tabSeg.append(b);
    }
    this.submitBtn.addEventListener("click", () => this.openSubmit());

    const header = h("header", { class: "topbar client-top" },
      h("div", { class: "client-title" },
        info.studio ? h("div", { class: "studio", text: info.studio }) : null,
        h("div", { class: "title", text: info.title ?? "" })),
      h("div", { class: "spacer" }),
      h("button", { class: "btn help-btn", text: "?", title: t("helpTitle"), "aria-label": t("helpTitle"),
        onclick: () => this.showHelp() }),
      this.submitBtn,
      this.zipLink);
    const toolbar = h("div", { class: "toolbar" }, this.tabSeg, this.countEl, this.filterSeg, modes);

    const noteBtn = h("button", { class: "ctl note-btn", text: t("note"), "data-testid": "note-btn",
      onclick: () => this.openNote() });
    this.proof = new PhotoBrowser<CPhoto>(this.proofBody, {
      text: this.browserText(),
      autoAdvance: false,
      showPick: true,
      canPick: () => !this.info?.locked,
      lockedMessage: () => t("lockedToast"),
      save: (p, picked) => this.saveSelected(p, picked),
      describe: (p) => ({
        badges: p.note ? [h("span", { class: "badge note", text: `✎ ${p.note}`, title: p.note })] : [],
        cell: { note: !!p.note },
      }),
      onRender: () => this.renderHeader(),
      onAction: (a) => { if (a === "help") this.showHelp(); },
      extraControls: [noteBtn],
      modalOpen: () => this.modalOpen,
      initialMode: "grid",
      pickClass: "heart",
    });
    this.root.replaceChildren(header, toolbar, this.noticeEl, this.proofBody, this.finalBody);
    this.root.classList.add("client-root");
  }

  private browserText() {
    return {
      pick: t("selectBtn"), picked: t("selectedBtn"), pickedBadge: t("selectedBadge"),
      prev: t("prev"), next: t("next"), zoom: t("zoom"), first: t("first"), last: t("last"),
      empty: t("empty"),
      emptyFilter: (f: Filter) => (f === "picked" ? t("emptySelected") : f === "unpicked" ? t("emptyUnselected") : t("empty")),
      loadingOriginal: t("loadingOriginal"), zoomHint: t("zoomHint"), outOfFilter: t("outOfFilter"), checkMark: "♥",
    };
  }

  private current(): PhotoBrowser<CPhoto> | PhotoBrowser<DPhoto> | null {
    return this.view === "proof" ? this.proof : this.final;
  }

  private renderHeader(): void {
    const info = this.info;
    const b = this.current();
    if (!info || !b) return;
    const proofing = this.view === "proof";
    if (proofing && this.proof) {
      const n = this.proof.pickedCount();
      this.countEl.textContent = info.limit ? t("counterLimit", { n, limit: info.limit }) : t("counter", { n });
      this.countEl.classList.toggle("over", !!info.limit && n > info.limit);
      for (const f of Object.keys(this.filterBtns) as Filter[]) {
        this.filterBtns[f].classList.toggle("active", f === this.proof.filter);
      }
    } else {
      this.countEl.textContent = "";
      this.zipLink.textContent = t("downloadAll");
    }
    this.countEl.hidden = !proofing;
    this.filterSeg.hidden = !proofing;
    this.submitBtn.hidden = !proofing;
    this.zipLink.hidden = proofing;
    for (const m of Object.keys(this.modeBtns) as Mode[]) {
      this.modeBtns[m].classList.toggle("active", m === b.mode);
    }
    for (const v of Object.keys(this.tabBtns) as ("proof" | "final")[]) {
      this.tabBtns[v].classList.toggle("active", v === this.view);
    }
    this.submitBtn.textContent = info.locked === "submitted" ? t("submitted")
      : info.locked === "deadline" ? t("closed") : t("submit");
    this.submitBtn.disabled = !!info.locked;
    this.root.classList.toggle("locked", !!info.locked);
  }

  private renderNotice(): void {
    const info = this.info!;
    // 第一行：锁定说明（整句）；第二行：张数、截止、删除日期
    let head = "";
    const facts: string[] = [];
    let warn = false;
    if (this.view === "final") {
      const size = this.finalItems.reduce((s, d) => s + d.size, 0);
      head = t("deliveryInfo", { n: this.finalItems.length, size: fmtBytes(size) });
    } else if (info.locked === "submitted") {
      head = t("lockedSubmitted", { date: info.submitted_at ? fmtDay(info.submitted_at) : "" });
      warn = true;
    } else if (info.locked === "deadline") {
      head = t("lockedDeadline", { date: fmtDay(info.deadline_at!) });
      warn = true;
    } else {
      if (info.limit) facts.push(t("limitInfo", { limit: info.limit }));
      if (info.deadline_at) facts.push(t("deadlineInfo", { date: fmtDay(info.deadline_at) }));
    }
    if (info.delete_at) facts.push(t("deleteInfo", { date: fmtDay(info.delete_at) }));
    const text = [head, facts.join(" · ")].filter(Boolean).join("\n");
    this.noticeEl.textContent = text;
    this.noticeEl.hidden = !text;
    this.noticeEl.classList.toggle("warn", warn);
  }

  // ------------------------------------------------------------------ 选片
  private async saveSelected(p: CPhoto, picked: boolean): Promise<void> {
    try {
      await api.update(p.id, { selected: picked });
    } catch (e) {
      const err = e as ReqError;
      if (err.code === "locked") await this.reloadInfo();
      throw new Error(err.code === "locked" ? t("lockedToast") : t("saveFailed", { msg: err.message }));
    }
  }

  private async reloadInfo(): Promise<void> {
    try {
      this.info = await api.info();
      this.renderNotice();
      this.renderHeader();
    } catch {
      /* 忽略，下次操作再试 */
    }
  }

  private openNote(): void {
    const p = this.proof?.current();
    if (!p) return;
    if (this.info?.locked) {
      toast(t("lockedToast"), 3000);
      return;
    }
    this.modalOpen = true;
    const m = openModal(t("noteTitle"), () => { this.modalOpen = false; });
    m.el.dataset.testid = "note-dialog";
    const ta = h("textarea", { class: "input textarea", rows: "4", maxlength: "1000",
      placeholder: t("notePlaceholder") }) as HTMLTextAreaElement;
    ta.value = p.note;
    m.body.append(h("p", { class: "muted small", text: p.name }), ta);
    const save = h("button", { class: "btn primary", text: t("save") }) as HTMLButtonElement;
    save.addEventListener("click", async () => {
      save.disabled = true;
      try {
        const r = await api.update(p.id, { note: ta.value });
        p.note = r.note;
        toast(r.note ? t("noteSaved") : t("noteCleared"));
        m.close();
        this.proof?.refreshCurrent();
      } catch (e) {
        const err = e as ReqError;
        if (err.code === "locked") void this.reloadInfo();
        toast(err.code === "locked" ? t("lockedToast") : t("saveFailed", { msg: err.message }), 4000);
        save.disabled = false;
      }
    });
    m.actions.append(h("button", { class: "btn", text: t("cancel"), onclick: () => m.close() }), save);
    ta.focus();
  }

  private openSubmit(): void {
    const info = this.info!;
    const n = this.proof?.pickedCount() ?? 0;
    if (n === 0) {
      toast(t("nothingSelected"));
      return;
    }
    const limit = info.limit ?? 0;
    const over = limit ? n - limit : 0;
    this.modalOpen = true;
    const m = openModal(t("submitTitle"), () => { this.modalOpen = false; });
    m.el.dataset.testid = "submit-dialog";
    if (over > 0 && info.limit_mode === "block") {
      m.body.append(h("p", { class: "warn", text: t("overBlock", { limit, n, over }) }));
      m.actions.append(h("button", { class: "btn", text: t("close"), onclick: () => m.close() }));
      return;
    }
    m.body.append(h("p", { class: over > 0 ? "warn" : "",
      text: over > 0 ? t("overWarn", { n, limit, over }) : t("submitConfirm", { n }) }));
    const ok = h("button", { class: "btn primary", text: t("submit"), "data-testid": "submit-confirm" }) as HTMLButtonElement;
    ok.addEventListener("click", async () => {
      ok.disabled = true;
      try {
        await api.submit(over > 0);
        m.close();
        toast(t("submitDone"), 3000);
        await this.reloadInfo();
        this.proof?.render();
      } catch (e) {
        const err = e as ReqError;
        m.close();
        toast(err.message, 4000);
        void this.reloadInfo();
      }
    });
    m.actions.append(h("button", { class: "btn", text: t("cancel"), onclick: () => m.close() }), ok);
  }

  // ------------------------------------------------------------------ 成片
  private async loadDelivery(): Promise<void> {
    let photos;
    try {
      photos = (await api.delivery()).photos;
    } catch {
      return;
    }
    if (photos.length === 0) return;
    this.finalItems = photos.map((p) => ({
      id: p.id, name: p.name, picked: false, size: p.size,
      thumb: `${base}/d/${p.id}/thumb`, preview: `${base}/d/${p.id}/preview`,
      original: `${base}/d/${p.id}/file`, file: `${base}/d/${p.id}/file`,
    }));
    this.zipLink.setAttribute("href", `${base}/d/all.zip`);
    this.downloadLink.textContent = t("download");
    this.downloadLink.setAttribute("download", "");
    this.final = new PhotoBrowser<DPhoto>(this.finalBody, {
      text: this.browserText(),
      autoAdvance: false,
      showPick: false,
      canPick: () => false,
      save: async () => {},
      onRender: () => this.renderHeader(),
      onCurrent: (p) => {
        if (p) this.downloadLink.setAttribute("href", p.file);
      },
      onAction: (a) => { if (a === "help") this.showHelp(); },
      extraControls: [this.downloadLink],
      modalOpen: () => this.modalOpen,
      initialMode: "grid",
    });
    this.final.setActive(false);
    this.final.setItems(this.finalItems);
    this.tabSeg.hidden = false;
    this.renderHeader();
  }

  private setView(v: "proof" | "final"): void {
    if (this.view === v) return;
    this.view = v;
    this.proofBody.hidden = v !== "proof";
    this.finalBody.hidden = v !== "final";
    this.proof?.setActive(v === "proof");
    this.final?.setActive(v === "final");
    this.renderNotice();
    this.renderHeader();
  }

  // ------------------------------------------------------------------ 说明
  private introBody(): HTMLElement[] {
    const info = this.info!;
    const out: HTMLElement[] = [];
    if (info.message) {
      out.push(h("h3", { text: t("fromPhotographer") }), h("p", { class: "pre", text: info.message }));
    }
    const facts: string[] = [];
    if (info.locked === "submitted") {
      facts.push(t("lockedSubmitted", { date: info.submitted_at ? fmtDay(info.submitted_at) : "" }));
    } else if (info.locked === "deadline") {
      facts.push(t("lockedDeadline", { date: fmtDay(info.deadline_at!) }));
    } else {
      if (info.limit) facts.push(t("limitInfo", { limit: info.limit }));
      if (info.deadline_at) facts.push(t("deadlineInfo", { date: fmtDay(info.deadline_at) }));
    }
    if (info.delete_at) facts.push(t("deleteInfo", { date: fmtDay(info.delete_at) }));
    if (facts.length) out.push(h("p", { class: "facts", text: facts.join("\n") }));
    out.push(h("h3", { text: t("helpTitle") }),
      h("ol", { class: "help-list" }, ...helpLines().map((l) => h("li", { text: l }))));
    return out;
  }

  private showWelcome(): void {
    this.modalOpen = true;
    const info = this.info!;
    const m = openModal(info.title || t("helpTitle"), () => {
      this.modalOpen = false;
      try {
        localStorage.setItem(seenKey(), "1");
      } catch {
        /* 隐私模式下存不了，下次再显示 */
      }
    });
    m.el.dataset.testid = "welcome";
    m.body.append(...this.introBody());
    m.actions.append(h("button", { class: "btn primary", text: t("start"), onclick: () => m.close() }));
  }

  private showHelp(): void {
    this.modalOpen = true;
    const m = openModal(t("helpTitle"), () => { this.modalOpen = false; });
    m.body.append(...this.introBody());
    m.actions.append(h("button", { class: "btn", text: t("close"), onclick: () => m.close() }));
  }
}

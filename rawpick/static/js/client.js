// 客户页面：输入密码 → 选片（选中、备注、提交）→ 下载成片。
// 地址是 /g/<随机串>，所有接口都在这个前缀下面。
import { PhotoBrowser } from "./browser.js";
import { detectLang, fmtDay, helpLines, setLang, t } from "./i18n.js";
import { fmtBytes, h, openModal, toast } from "./util.js";
class ReqError extends Error {
    status;
    code;
    data;
    constructor(message, status, code, data) {
        super(message);
        this.status = status;
        this.code = code;
        this.data = data;
    }
}
const token = decodeURIComponent(location.pathname.split("/")[2] || "");
const base = `/g/${encodeURIComponent(token)}`;
async function req(method, path, body) {
    let r;
    try {
        r = await fetch(base + path, {
            method,
            credentials: "same-origin",
            headers: body === undefined ? {} : { "Content-Type": "application/json" },
            body: body === undefined ? undefined : JSON.stringify(body),
        });
    }
    catch {
        throw new ReqError(t("networkError"), 0, "network", {});
    }
    const data = await r.json().catch(() => ({}));
    if (!r.ok)
        throw new ReqError(data.error || `HTTP ${r.status}`, r.status, data.code || "", data);
    return data;
}
const api = {
    info: () => req("GET", "/api/info"),
    login: (password) => req("POST", "/api/login", { password }),
    photos: () => req("GET", "/api/photos"),
    update: (pid, patch) => req("PUT", `/api/photos/${pid}`, patch),
    submit: (confirm) => req("POST", "/api/submit", { confirm }),
    delivery: () => req("GET", "/api/delivery"),
};
function seenKey() {
    return `rawpick.g.${token}.seen`;
}
export class ClientApp {
    root;
    info = null;
    modalOpen = false;
    view = "proof";
    proof = null;
    final = null;
    finalItems = [];
    countEl = h("span", { class: "count", "data-testid": "count" });
    noticeEl = h("div", { class: "client-notice", "data-testid": "client-notice" });
    submitBtn = h("button", { class: "btn primary", "data-testid": "submit-btn" });
    filterBtns = {};
    modeBtns = {};
    tabBtns = {};
    filterSeg = h("div", { class: "seg", role: "group" });
    tabSeg = h("div", { class: "seg", role: "group", hidden: true });
    zipLink = h("a", { class: "btn primary", hidden: true, "data-testid": "zip-link" });
    proofBody = h("div", { class: "body" });
    finalBody = h("div", { class: "body", hidden: true });
    downloadLink = h("a", { class: "ctl dl-btn", "data-testid": "download-link" });
    constructor(root) {
        this.root = root;
        setLang(detectLang("auto"));
        void this.start();
    }
    // ------------------------------------------------------------------ 启动
    async start() {
        this.message(t("loading"));
        let info;
        try {
            info = await api.info();
        }
        catch (e) {
            const err = e;
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
    message(text, withRefresh = false) {
        this.root.replaceChildren(h("div", { class: "center-card" }, this.info?.studio ? h("div", { class: "studio", text: this.info.studio }) : null, h("p", { "data-testid": "client-message", text }), withRefresh ? h("button", { class: "btn", text: t("refresh"), onclick: () => location.reload() }) : null));
    }
    passwordScreen(info) {
        const input = h("input", { class: "input", type: "password", placeholder: t("passwordPlaceholder"),
            autocomplete: "current-password", "data-testid": "password" });
        const err = h("p", { class: "error", hidden: true });
        const btn = h("button", { class: "btn primary", type: "submit", text: t("enter") });
        const form = h("form", { class: "center-card" }, info.studio ? h("div", { class: "studio", text: info.studio }) : null, h("h1", { text: t("passwordTitle") }), h("p", { class: "muted", text: t("passwordHint") }), input, btn, err);
        form.addEventListener("submit", async (e) => {
            e.preventDefault();
            if (!input.value)
                return;
            btn.disabled = true;
            err.hidden = true;
            try {
                await api.login(input.value);
                await this.start();
            }
            catch (ex) {
                const x = ex;
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
    async mainScreen() {
        const info = this.info;
        let photos;
        try {
            photos = (await api.photos()).photos;
        }
        catch (e) {
            this.message(e.message);
            return;
        }
        this.buildLayout(info);
        const items = photos.map((p) => ({
            id: p.id, name: p.name, picked: p.selected, note: p.note,
            thumb: `${base}/img/${p.id}/thumb`, preview: `${base}/img/${p.id}/preview`, original: null,
        }));
        this.proof.setItems(items);
        this.renderNotice();
        if (info.delivery)
            void this.loadDelivery();
        try {
            if (!localStorage.getItem(seenKey()))
                this.showWelcome();
        }
        catch {
            this.showWelcome();
        }
    }
    buildLayout(info) {
        const filterLabel = { all: t("all"), picked: t("selected"), unpicked: t("unselected") };
        for (const f of ["all", "picked", "unpicked"]) {
            const b = h("button", { class: "seg-btn", text: filterLabel[f], "data-filter": f,
                onclick: () => this.proof?.setFilter(f) });
            this.filterBtns[f] = b;
            this.filterSeg.append(b);
        }
        const modes = h("div", { class: "seg", role: "group" });
        for (const [m, label] of [["grid", t("grid")], ["loupe", t("loupe")]]) {
            const b = h("button", { class: "seg-btn", text: label, "data-mode": m,
                onclick: () => this.current()?.setMode(m) });
            this.modeBtns[m] = b;
            modes.append(b);
        }
        for (const [v, label] of [["proof", t("tabProof")], ["final", t("tabFinal")]]) {
            const b = h("button", { class: "seg-btn", text: label, "data-tab": v,
                onclick: () => this.setView(v) });
            this.tabBtns[v] = b;
            this.tabSeg.append(b);
        }
        this.submitBtn.addEventListener("click", () => this.openSubmit());
        const header = h("header", { class: "topbar client-top" }, h("div", { class: "client-title" }, info.studio ? h("div", { class: "studio", text: info.studio }) : null, h("div", { class: "title", text: info.title ?? "" })), h("div", { class: "spacer" }), h("button", { class: "btn help-btn", text: "?", title: t("helpTitle"), "aria-label": t("helpTitle"),
            onclick: () => this.showHelp() }), this.submitBtn, this.zipLink);
        const toolbar = h("div", { class: "toolbar" }, this.tabSeg, this.countEl, this.filterSeg, modes);
        const noteBtn = h("button", { class: "ctl note-btn", text: t("note"), "data-testid": "note-btn",
            onclick: () => this.openNote() });
        this.proof = new PhotoBrowser(this.proofBody, {
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
            onAction: (a) => { if (a === "help")
                this.showHelp(); },
            extraControls: [noteBtn],
            modalOpen: () => this.modalOpen,
            initialMode: "grid",
            pickClass: "heart",
        });
        this.root.replaceChildren(header, toolbar, this.noticeEl, this.proofBody, this.finalBody);
        this.root.classList.add("client-root");
    }
    browserText() {
        return {
            pick: t("selectBtn"), picked: t("selectedBtn"), pickedBadge: t("selectedBadge"),
            prev: t("prev"), next: t("next"), zoom: t("zoom"), first: t("first"), last: t("last"),
            empty: t("empty"),
            emptyFilter: (f) => (f === "picked" ? t("emptySelected") : f === "unpicked" ? t("emptyUnselected") : t("empty")),
            loadingOriginal: t("loadingOriginal"), zoomHint: t("zoomHint"), outOfFilter: t("outOfFilter"), checkMark: "♥",
        };
    }
    current() {
        return this.view === "proof" ? this.proof : this.final;
    }
    renderHeader() {
        const info = this.info;
        const b = this.current();
        if (!info || !b)
            return;
        const proofing = this.view === "proof";
        if (proofing && this.proof) {
            const n = this.proof.pickedCount();
            this.countEl.textContent = info.limit ? t("counterLimit", { n, limit: info.limit }) : t("counter", { n });
            this.countEl.classList.toggle("over", !!info.limit && n > info.limit);
            for (const f of Object.keys(this.filterBtns)) {
                this.filterBtns[f].classList.toggle("active", f === this.proof.filter);
            }
        }
        else {
            this.countEl.textContent = "";
            this.zipLink.textContent = t("downloadAll");
        }
        this.countEl.hidden = !proofing;
        this.filterSeg.hidden = !proofing;
        this.submitBtn.hidden = !proofing;
        this.zipLink.hidden = proofing;
        for (const m of Object.keys(this.modeBtns)) {
            this.modeBtns[m].classList.toggle("active", m === b.mode);
        }
        for (const v of Object.keys(this.tabBtns)) {
            this.tabBtns[v].classList.toggle("active", v === this.view);
        }
        this.submitBtn.textContent = info.locked === "submitted" ? t("submitted")
            : info.locked === "deadline" ? t("closed") : t("submit");
        this.submitBtn.disabled = !!info.locked;
        this.root.classList.toggle("locked", !!info.locked);
    }
    renderNotice() {
        const info = this.info;
        // 第一行：锁定说明（整句）；第二行：张数、截止、删除日期
        let head = "";
        const facts = [];
        let warn = false;
        if (this.view === "final") {
            const size = this.finalItems.reduce((s, d) => s + d.size, 0);
            head = t("deliveryInfo", { n: this.finalItems.length, size: fmtBytes(size) });
        }
        else if (info.locked === "submitted") {
            head = t("lockedSubmitted", { date: info.submitted_at ? fmtDay(info.submitted_at) : "" });
            warn = true;
        }
        else if (info.locked === "deadline") {
            head = t("lockedDeadline", { date: fmtDay(info.deadline_at) });
            warn = true;
        }
        else {
            if (info.limit)
                facts.push(t("limitInfo", { limit: info.limit }));
            if (info.deadline_at)
                facts.push(t("deadlineInfo", { date: fmtDay(info.deadline_at) }));
        }
        if (info.delete_at)
            facts.push(t("deleteInfo", { date: fmtDay(info.delete_at) }));
        const text = [head, facts.join(" · ")].filter(Boolean).join("\n");
        this.noticeEl.textContent = text;
        this.noticeEl.hidden = !text;
        this.noticeEl.classList.toggle("warn", warn);
    }
    // ------------------------------------------------------------------ 选片
    async saveSelected(p, picked) {
        try {
            await api.update(p.id, { selected: picked });
        }
        catch (e) {
            const err = e;
            if (err.code === "locked")
                await this.reloadInfo();
            throw new Error(err.code === "locked" ? t("lockedToast") : t("saveFailed", { msg: err.message }));
        }
    }
    async reloadInfo() {
        try {
            this.info = await api.info();
            this.renderNotice();
            this.renderHeader();
        }
        catch {
            /* 忽略，下次操作再试 */
        }
    }
    openNote() {
        const p = this.proof?.current();
        if (!p)
            return;
        if (this.info?.locked) {
            toast(t("lockedToast"), 3000);
            return;
        }
        this.modalOpen = true;
        const m = openModal(t("noteTitle"), () => { this.modalOpen = false; });
        m.el.dataset.testid = "note-dialog";
        const ta = h("textarea", { class: "input textarea", rows: "4", maxlength: "1000",
            placeholder: t("notePlaceholder") });
        ta.value = p.note;
        m.body.append(h("p", { class: "muted small", text: p.name }), ta);
        const save = h("button", { class: "btn primary", text: t("save") });
        save.addEventListener("click", async () => {
            save.disabled = true;
            try {
                const r = await api.update(p.id, { note: ta.value });
                p.note = r.note;
                toast(r.note ? t("noteSaved") : t("noteCleared"));
                m.close();
                this.proof?.refreshCurrent();
            }
            catch (e) {
                const err = e;
                if (err.code === "locked")
                    void this.reloadInfo();
                toast(err.code === "locked" ? t("lockedToast") : t("saveFailed", { msg: err.message }), 4000);
                save.disabled = false;
            }
        });
        m.actions.append(h("button", { class: "btn", text: t("cancel"), onclick: () => m.close() }), save);
        ta.focus();
    }
    openSubmit() {
        const info = this.info;
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
        const ok = h("button", { class: "btn primary", text: t("submit"), "data-testid": "submit-confirm" });
        ok.addEventListener("click", async () => {
            ok.disabled = true;
            try {
                await api.submit(over > 0);
                m.close();
                toast(t("submitDone"), 3000);
                await this.reloadInfo();
                this.proof?.render();
            }
            catch (e) {
                const err = e;
                m.close();
                toast(err.message, 4000);
                void this.reloadInfo();
            }
        });
        m.actions.append(h("button", { class: "btn", text: t("cancel"), onclick: () => m.close() }), ok);
    }
    // ------------------------------------------------------------------ 成片
    async loadDelivery() {
        let photos;
        try {
            photos = (await api.delivery()).photos;
        }
        catch {
            return;
        }
        if (photos.length === 0)
            return;
        this.finalItems = photos.map((p) => ({
            id: p.id, name: p.name, picked: false, size: p.size,
            thumb: `${base}/d/${p.id}/thumb`, preview: `${base}/d/${p.id}/preview`,
            original: `${base}/d/${p.id}/file`, file: `${base}/d/${p.id}/file`,
        }));
        this.zipLink.setAttribute("href", `${base}/d/all.zip`);
        this.downloadLink.textContent = t("download");
        this.downloadLink.setAttribute("download", "");
        this.final = new PhotoBrowser(this.finalBody, {
            text: this.browserText(),
            autoAdvance: false,
            showPick: false,
            canPick: () => false,
            save: async () => { },
            onRender: () => this.renderHeader(),
            onCurrent: (p) => {
                if (p)
                    this.downloadLink.setAttribute("href", p.file);
            },
            onAction: (a) => { if (a === "help")
                this.showHelp(); },
            extraControls: [this.downloadLink],
            modalOpen: () => this.modalOpen,
            initialMode: "grid",
        });
        this.final.setActive(false);
        this.final.setItems(this.finalItems);
        this.tabSeg.hidden = false;
        this.renderHeader();
    }
    setView(v) {
        if (this.view === v)
            return;
        this.view = v;
        this.proofBody.hidden = v !== "proof";
        this.finalBody.hidden = v !== "final";
        this.proof?.setActive(v === "proof");
        this.final?.setActive(v === "final");
        this.renderNotice();
        this.renderHeader();
    }
    // ------------------------------------------------------------------ 说明
    introBody() {
        const info = this.info;
        const out = [];
        if (info.message) {
            out.push(h("h3", { text: t("fromPhotographer") }), h("p", { class: "pre", text: info.message }));
        }
        const facts = [];
        if (info.locked === "submitted") {
            facts.push(t("lockedSubmitted", { date: info.submitted_at ? fmtDay(info.submitted_at) : "" }));
        }
        else if (info.locked === "deadline") {
            facts.push(t("lockedDeadline", { date: fmtDay(info.deadline_at) }));
        }
        else {
            if (info.limit)
                facts.push(t("limitInfo", { limit: info.limit }));
            if (info.deadline_at)
                facts.push(t("deadlineInfo", { date: fmtDay(info.deadline_at) }));
        }
        if (info.delete_at)
            facts.push(t("deleteInfo", { date: fmtDay(info.delete_at) }));
        if (facts.length)
            out.push(h("p", { class: "facts", text: facts.join("\n") }));
        out.push(h("h3", { text: t("helpTitle") }), h("ol", { class: "help-list" }, ...helpLines().map((l) => h("li", { text: l }))));
        return out;
    }
    showWelcome() {
        this.modalOpen = true;
        const info = this.info;
        const m = openModal(info.title || t("helpTitle"), () => {
            this.modalOpen = false;
            try {
                localStorage.setItem(seenKey(), "1");
            }
            catch {
                /* 隐私模式下存不了，下次再显示 */
            }
        });
        m.el.dataset.testid = "welcome";
        m.body.append(...this.introBody());
        m.actions.append(h("button", { class: "btn primary", text: t("start"), onclick: () => m.close() }));
    }
    showHelp() {
        this.modalOpen = true;
        const m = openModal(t("helpTitle"), () => { this.modalOpen = false; });
        m.body.append(...this.introBody());
        m.actions.append(h("button", { class: "btn", text: t("close"), onclick: () => m.close() }));
    }
}

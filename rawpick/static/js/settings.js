// 设置页：快捷键、找回标记、关于。设置存在服务器上，所有设备共用。
import { api } from "./api.js";
import { ACTIONS, RESERVED, SCOPE_LABEL, comboOf, getBindings, keyLabel, resetBindings, saveBindings, scopesOverlap, } from "./keymap.js";
import { renderOrphans } from "./orphans.js";
import { h, toast } from "./util.js";
export class SettingsView {
    root;
    bindings = getBindings();
    capturing = null;
    keysBox = h("div", { class: "keys-box" });
    cleanups = [];
    constructor(root) {
        this.root = root;
        document.title = "设置 · rawpick";
        this.build();
        const onKey = (e) => this.onKey(e);
        window.addEventListener("keydown", onKey, true);
        this.cleanups.push(() => window.removeEventListener("keydown", onKey, true));
    }
    destroy() {
        for (const fn of this.cleanups)
            fn();
    }
    build() {
        const orphansBox = h("div", { class: "orphans-box" });
        const about = h("dl", { class: "about" });
        this.root.replaceChildren(h("header", { class: "topbar" }, h("a", { class: "btn back", href: "#/", text: "‹ 返回" }), h("div", { class: "title", text: "设置" })), h("main", { class: "settings" }, h("section", { class: "settings-section", id: "shortcuts" }, h("h2", { text: "快捷键" }), h("p", { class: "muted small",
            text: "每个操作可以绑定多个键。点“添加”后按下要用的键，按 Esc 取消。修改会自动保存，所有设备共用。" }), this.keysBox, h("div", { class: "settings-actions" }, h("button", { class: "btn", text: "恢复默认快捷键", onclick: () => this.reset() }))), h("section", { class: "settings-section", id: "orphans" }, h("h2", { text: "找回标记" }), h("p", { class: "muted small",
            text: "文件夹改名或移动后，它的标记会留在数据库里，对不上新文件夹。照片完全相同的会自动关联；对不上的可以在这里手动指定。" }), orphansBox), h("section", { class: "settings-section" }, h("h2", { text: "关于" }), about)));
        this.renderKeys();
        void renderOrphans(orphansBox);
        api.health().then((hl) => {
            about.append(h("dt", { text: "版本" }), h("dd", { text: hl.version }), h("dt", { text: "照片根目录" }), h("dd", { text: hl.photo_root }), h("dt", { text: "数据目录" }), h("dd", { text: hl.data_dir }));
        }, () => about.append(h("dd", { class: "error", text: "读取失败" })));
    }
    renderKeys() {
        const rows = ACTIONS.map((a) => {
            const chips = h("div", { class: "chips" });
            for (const combo of this.bindings[a.id]) {
                chips.append(h("span", { class: "chip" }, h("kbd", { text: keyLabel(combo) }), h("button", { class: "chip-x", "aria-label": `删除 ${keyLabel(combo)}`, text: "×",
                    onclick: () => this.remove(a.id, combo) })));
            }
            const adding = this.capturing === a.id;
            chips.append(h("button", {
                class: adding ? "btn small capturing" : "btn small",
                "data-action": a.id,
                text: adding ? "请按下要用的键……" : "+ 添加",
                onclick: () => { this.capturing = adding ? null : a.id; this.renderKeys(); },
            }));
            const scope = SCOPE_LABEL[a.scope];
            return h("tr", { "data-action": a.id }, h("td", {}, h("div", { text: a.label }), scope ? h("div", { class: "muted small", text: scope }) : null), h("td", {}, chips));
        });
        rows.push(h("tr", {}, h("td", {}, h("div", { text: "退出放大、关闭弹窗" }), h("div", { class: "muted small", text: "固定，不能修改" })), h("td", {}, h("div", { class: "chips" }, h("span", { class: "chip" }, h("kbd", { text: "Esc" }))))));
        this.keysBox.replaceChildren(h("table", { class: "keys-table" }, ...rows));
    }
    onKey(e) {
        if (!this.capturing)
            return;
        e.preventDefault();
        e.stopPropagation();
        if (e.code === "Escape") {
            this.capturing = null;
            this.renderKeys();
            return;
        }
        const combo = comboOf(e);
        if (!combo)
            return; // 只按了修饰键，继续等
        if (RESERVED.has(e.code)) {
            toast("Esc 和 Tab 不能设为快捷键");
            return;
        }
        const target = ACTIONS.find((a) => a.id === this.capturing);
        this.capturing = null;
        if (this.bindings[target.id].includes(combo)) {
            toast(`「${keyLabel(combo)}」已经在这里了`);
            this.renderKeys();
            return;
        }
        // 同一个键不能在同一视图里对应两个操作：从原来的操作上移走
        for (const other of ACTIONS) {
            if (other.id === target.id || !scopesOverlap(other.scope, target.scope))
                continue;
            const i = this.bindings[other.id].indexOf(combo);
            if (i >= 0) {
                this.bindings[other.id].splice(i, 1);
                toast(`「${keyLabel(combo)}」原来用于“${other.label}”，已移到这里`, 3500);
            }
        }
        this.bindings[target.id].push(combo);
        void this.save();
    }
    remove(id, combo) {
        this.bindings[id] = this.bindings[id].filter((c) => c !== combo);
        void this.save();
    }
    async save() {
        this.renderKeys();
        try {
            await saveBindings(this.bindings);
            this.bindings = getBindings();
            this.renderKeys();
        }
        catch (e) {
            toast(`保存失败：${e.message}`, 4000);
            this.bindings = getBindings();
            this.renderKeys();
        }
    }
    async reset() {
        try {
            await resetBindings();
            this.bindings = getBindings();
            this.capturing = null;
            this.renderKeys();
            toast("已恢复默认快捷键");
        }
        catch (e) {
            toast(`操作失败：${e.message}`, 4000);
        }
    }
}

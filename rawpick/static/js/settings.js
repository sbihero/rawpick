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
        const galleryBox = h("div", { class: "form" });
        const about = h("dl", { class: "about" });
        this.root.replaceChildren(h("header", { class: "topbar" }, h("a", { class: "btn back", href: "#/", text: "‹ 返回" }), h("div", { class: "title", text: "设置" })), h("main", { class: "settings" }, h("section", { class: "settings-section", id: "shortcuts" }, h("h2", { text: "快捷键" }), h("p", { class: "muted small",
            text: "每个操作可以绑定多个键。点“添加”后按下要用的键，按 Esc 取消。修改会自动保存，所有设备共用。" }), this.keysBox, h("div", { class: "settings-actions" }, h("button", { class: "btn", text: "恢复默认快捷键", onclick: () => this.reset() }))), h("section", { class: "settings-section", id: "orphans" }, h("h2", { text: "找回标记" }), h("p", { class: "muted small",
            text: "文件夹改名或移动后，它的标记会留在数据库里，对不上新文件夹。照片完全相同的会自动关联；对不上的可以在这里手动指定。" }), orphansBox), h("section", { class: "settings-section", id: "gallery-settings" }, h("h2", { text: "客户相册" }), h("p", { class: "muted small",
            text: "新发布的相册使用这里的设置；已经发布的相册不受影响。" }), galleryBox), h("section", { class: "settings-section" }, h("h2", { text: "关于" }), about)));
        this.renderKeys();
        void renderOrphans(orphansBox);
        void this.renderGallerySettings(galleryBox);
        api.health().then((hl) => {
            about.append(h("dt", { text: "版本" }), h("dd", { text: hl.version }), h("dt", { text: "照片根目录" }), h("dd", { text: hl.photo_root }), h("dt", { text: "数据目录" }), h("dd", { text: hl.data_dir }), h("dt", { text: "客户端端口" }), h("dd", { text: hl.client_port ? String(hl.client_port) : "未启动" }), h("dt", { text: "水印字体" }), h("dd", { text: hl.watermark_font || "没有找到，中文水印会显示成方框" }));
        }, () => about.append(h("dd", { class: "error", text: "读取失败" })));
    }
    async renderGallerySettings(box) {
        let s, hl;
        try {
            [s, hl] = await Promise.all([api.settings(), api.health()]);
        }
        catch (e) {
            box.replaceChildren(h("p", { class: "error", text: `读取失败：${e.message}` }));
            return;
        }
        const auto = hl.client_port ? `${location.protocol}//${location.hostname}:${hl.client_port}` : "";
        const wm = h("input", { class: "input", maxlength: "40", value: s.watermark_text ?? "严记肘子铺",
            name: "watermark_text" });
        const base = h("input", { class: "input", maxlength: "200", value: s.client_base_url ?? "",
            name: "client_base_url", placeholder: auto ? `自动：${auto}` : "" });
        const saveOne = async (key, value, okText) => {
            try {
                await api.saveSettings({ [key]: value });
                toast(okText);
            }
            catch (e) {
                toast(e.message, 4000);
            }
        };
        box.replaceChildren(h("label", { class: "field" }, h("span", { class: "field-label", text: "水印文字" }), h("div", { class: "row" }, wm, h("button", { class: "btn", text: "保存", "data-testid": "save-watermark",
            onclick: () => void saveOne("watermark_text", wm.value.trim() || null, "已保存水印文字") })), h("span", { class: "field-hint muted small",
            text: "加在客户看到的预览图中间，半透明。也作为工作室名显示在客户页面顶部。" })), h("label", { class: "field" }, h("span", { class: "field-label", text: "客户访问地址" }), h("div", { class: "row" }, base, h("button", { class: "btn", text: "保存",
            onclick: () => void saveOne("client_base_url", base.value.trim() || null, base.value.trim() ? "已保存客户访问地址" : "已恢复自动") })), h("span", { class: "field-hint muted small",
            text: "生成客户链接用的地址。留空时用“这台服务器的地址 + 客户端端口”，只在家里局域网能打开；"
                + "以后接上域名和隧道后，填成 https://你的域名。" })));
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

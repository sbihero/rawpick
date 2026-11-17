// 操作说明面板：键盘（跟随设置里的快捷键）、鼠标、触屏。
import { ACTIONS, SCOPE_LABEL, getBindings, keyLabel } from "./keymap.js";
import { h, openModal } from "./util.js";
function keys(combos) {
    if (combos.length === 0)
        return h("span", { class: "muted", text: "（未设置）" });
    const span = h("span", { class: "keys" });
    combos.forEach((c) => span.append(h("kbd", { text: keyLabel(c) })));
    return span;
}
function table(rows) {
    return h("table", { class: "help-table" }, ...rows.map(([what, how]) => h("tr", {}, h("td", { text: what }), h("td", {}, typeof how === "string" ? how : how))));
}
export function showHelp(onClose) {
    const m = openModal("操作说明", onClose);
    m.el.dataset.testid = "help-panel";
    const b = getBindings();
    const keyRows = ACTIONS.map((a) => {
        const scope = SCOPE_LABEL[a.scope];
        return [scope ? `${a.label}（${scope}）` : a.label, keys(b[a.id])];
    });
    keyRows.push(["退出放大、关闭弹窗", keys(["Escape"])]);
    m.body.append(h("h3", { text: "键盘" }), table(keyRows), h("p", { class: "muted small" }, "快捷键可以在 ", h("a", { href: "#/settings", onclick: () => m.close() }, "设置"), " 里修改。"), h("h3", { text: "鼠标" }), table([
        ["单击图片", "以点击位置为中心放大到 100%"],
        ["放大后拖动", "平移查看"],
        ["放大后单击（不拖动）", "退出放大"],
        ["网格里点缩略图", "进入单张视图"],
    ]), h("h3", { text: "触屏（iPad）" }), table([
        ["左右滑动", "上一张 / 下一张"],
        ["底部大按钮", "选取 / 取消选取，然后跳到下一张"],
        ["双击或双指张开", "放大到 100%"],
        ["放大后单指拖动", "平移查看"],
        ["双击或双指捏合", "退出放大"],
        ["网格里点缩略图", "进入单张视图"],
    ]));
    m.actions.append(h("button", { class: "btn", text: "关闭", onclick: () => m.close() }));
}

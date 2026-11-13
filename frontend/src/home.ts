// 首页：列出照片根目录下的所有出门目录。

import { api } from "./api.js";
import { fmtTime, h } from "./util.js";

export async function renderHome(root: HTMLElement): Promise<void> {
  root.replaceChildren(
    h("header", { class: "topbar" }, h("div", { class: "brand", text: "rawpick" })),
    h("main", { class: "home" }, h("p", { class: "muted", text: "正在读取照片目录……" })),
  );
  const main = root.querySelector("main")!;
  let data;
  try {
    data = await api.sessions();
  } catch (e) {
    main.replaceChildren(h("p", { class: "error", text: `读取失败：${(e as Error).message}` }));
    return;
  }
  const head = h("div", { class: "home-head" },
    h("h1", { text: "选择一次出门" }),
    h("div", { class: "muted", text: `照片根目录：${data.root}` }));
  if (data.sessions.length === 0) {
    main.replaceChildren(head, h("p", { class: "muted",
      text: "没有找到同时包含 jpg/ 和 raw/ 子目录的文件夹。" }));
    return;
  }
  const list = h("div", { class: "sessions" });
  for (const s of data.sessions) {
    const meta = s.scanned
      ? `共 ${s.total} 张 · 已选 ${s.picked}`
      : `约 ${s.total} 张 · 尚未打开`;
    const card = h("a", { class: "session-card", href: `#/s/${s.id}` },
      h("div", { class: "session-name", text: s.name }),
      h("div", { class: "session-meta", text: meta }),
      s.last_export_at
        ? h("div", { class: "session-meta muted", text: `上次导出 ${fmtTime(s.last_export_at)}` })
        : null);
    list.append(card);
  }
  main.replaceChildren(head, list);
}

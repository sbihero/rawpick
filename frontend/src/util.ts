// 小工具：建 DOM、提示、格式化。

type Child = Node | string | number | null | undefined | false;

export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Record<string, unknown> = {},
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = String(v);
    else if (k === "text") el.textContent = String(v);
    else if (k.startsWith("on") && typeof v === "function") {
      el.addEventListener(k.slice(2), v as EventListener);
    } else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, String(v));
  }
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    el.append(typeof c === "number" ? String(c) : c);
  }
  return el;
}

let toastTimer = 0;
export function toast(message: string, ms = 2200): void {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = message;
  el.classList.add("show");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => el.classList.remove("show"), ms);
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`;
}

export function fmtTime(ts: number | null): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const pad = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export interface Modal {
  el: HTMLElement;
  body: HTMLElement;
  actions: HTMLElement;
  close: () => void;
}

/** 打开一个模态面板。点背景或调用 close() 关闭。 */
export function openModal(title: string, onClose: () => void): Modal {
  const body = h("div", { class: "modal-body" });
  const actions = h("div", { class: "modal-actions" });
  const panel = h("div", { class: "modal", role: "dialog", "aria-label": title },
    h("h2", { text: title }), body, actions);
  const backdrop = h("div", { class: "backdrop" }, panel);
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    backdrop.remove();
    onClose();
  };
  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop) close();
  });
  document.body.append(backdrop);
  return { el: backdrop, body, actions, close };
}

/** 可展开的文件名清单。 */
export function nameList(names: string[], limit = 200): HTMLElement {
  const shown = names.slice(0, limit);
  const ul = h("ul", { class: "names" }, ...shown.map((n) => h("li", { text: n })));
  if (names.length > limit) ul.append(h("li", { text: `……另外 ${names.length - limit} 个` }));
  return ul;
}

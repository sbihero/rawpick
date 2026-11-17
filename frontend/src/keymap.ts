// 快捷键：动作定义、默认按键、用户自定义（存服务器）、按键匹配与显示。
//
// 按“物理按键”（KeyboardEvent.code）识别，不按输入的字符：
// 开着大写锁定或切到中文输入法时，A、D、F、Q 照样能用。
// 一个绑定写成 "KeyA"、"ArrowLeft"、"Shift+Slash"（也就是 ?）这样的字符串。

import { api } from "./api.js";

export type ActionId =
  | "prev" | "next" | "pick" | "unpick" | "zoom" | "up" | "down" | "grid" | "loupe" | "help";

export type Scope = "both" | "loupe" | "grid";

export interface ActionDef {
  id: ActionId;
  label: string;
  scope: Scope;
  defaults: string[];
}

export const ACTIONS: ActionDef[] = [
  { id: "prev", label: "上一张", scope: "both", defaults: ["KeyA", "ArrowLeft"] },
  { id: "next", label: "下一张", scope: "both", defaults: ["KeyD", "ArrowRight"] },
  { id: "pick", label: "选取，然后跳到下一张", scope: "both", defaults: ["KeyF", "Enter"] },
  { id: "unpick", label: "取消选取，然后跳到下一张", scope: "both", defaults: ["KeyQ", "Backspace"] },
  { id: "zoom", label: "100% 放大 / 退出放大", scope: "loupe", defaults: ["KeyZ", "Space"] },
  { id: "up", label: "上移一行", scope: "grid", defaults: ["KeyW", "ArrowUp"] },
  { id: "down", label: "下移一行", scope: "grid", defaults: ["KeyS", "ArrowDown"] },
  { id: "grid", label: "切换到网格视图", scope: "both", defaults: ["KeyG"] },
  { id: "loupe", label: "切换到单张视图", scope: "both", defaults: ["KeyE"] },
  { id: "help", label: "显示操作说明", scope: "both", defaults: ["Shift+Slash"] },
];

export const SCOPE_LABEL: Record<Scope, string> = { both: "", loupe: "仅单张视图", grid: "仅网格视图" };

/** 不能绑定的键：Esc 固定用于退出放大和关闭弹窗，Tab 留给浏览器。 */
export const RESERVED = new Set(["Escape", "Tab"]);
const MODIFIER_CODES = new Set([
  "ShiftLeft", "ShiftRight", "ControlLeft", "ControlRight", "AltLeft", "AltRight",
  "MetaLeft", "MetaRight", "CapsLock", "Fn", "OSLeft", "OSRight",
]);

export type Bindings = Record<ActionId, string[]>;

function defaultBindings(): Bindings {
  const b = {} as Bindings;
  for (const a of ACTIONS) b[a.id] = a.defaults.slice();
  return b;
}

let bindings: Bindings = defaultBindings();
let customized = false;

export function getBindings(): Bindings {
  const copy = {} as Bindings;
  for (const a of ACTIONS) copy[a.id] = bindings[a.id].slice();
  return copy;
}

export function isCustomized(): boolean {
  return customized;
}

function applySaved(saved: Record<string, string[]> | undefined): void {
  bindings = defaultBindings();
  customized = false;
  if (!saved) return;
  for (const a of ACTIONS) {
    const v = saved[a.id];
    if (Array.isArray(v)) {
      bindings[a.id] = v.filter((x) => typeof x === "string");
      customized = true;
    }
  }
}

/** 启动时从服务器读取自定义快捷键；失败时用默认值。 */
export async function loadKeymap(): Promise<void> {
  try {
    const s = await api.settings();
    applySaved(s.shortcuts);
  } catch {
    applySaved(undefined);
  }
}

export async function saveBindings(next: Bindings): Promise<void> {
  const s = await api.saveSettings({ shortcuts: next });
  applySaved(s.shortcuts);
}

export async function resetBindings(): Promise<void> {
  const s = await api.saveSettings({ shortcuts: null });
  applySaved(s.shortcuts);
}

/** 把按键事件转成绑定字符串；纯修饰键或不支持的组合返回 null。 */
export function comboOf(e: KeyboardEvent): string | null {
  if (!e.code || MODIFIER_CODES.has(e.code)) return null;
  if (e.ctrlKey || e.metaKey || e.altKey) return null;
  return (e.shiftKey ? "Shift+" : "") + e.code;
}

function inScope(scope: Scope, mode: "loupe" | "grid"): boolean {
  return scope === "both" || scope === mode;
}

/** 两个动作的生效范围是否重叠（重叠时不能共用同一个键）。 */
export function scopesOverlap(a: Scope, b: Scope): boolean {
  return a === "both" || b === "both" || a === b;
}

export function actionFor(e: KeyboardEvent, mode: "loupe" | "grid"): ActionId | null {
  const combo = comboOf(e);
  if (!combo) return null;
  const find = (c: string) =>
    ACTIONS.find((a) => inScope(a.scope, mode) && bindings[a.id].includes(c))?.id ?? null;
  const exact = find(combo);
  if (exact) return exact;
  // 按着 Shift 打字母（比如大写）时，也认不带 Shift 的绑定
  if (e.shiftKey && e.code.startsWith("Key")) return find(e.code);
  return null;
}

const NAMES: Record<string, string> = {
  ArrowLeft: "←", ArrowRight: "→", ArrowUp: "↑", ArrowDown: "↓",
  Enter: "Enter", NumpadEnter: "小键盘 Enter", Backspace: "Backspace", Space: "空格",
  Delete: "Delete", Escape: "Esc", Slash: "/", Backslash: "\\", Comma: ",", Period: ".",
  Semicolon: ";", Quote: "'", BracketLeft: "[", BracketRight: "]", Minus: "-", Equal: "=",
  Backquote: "`", PageUp: "PageUp", PageDown: "PageDown", Home: "Home", End: "End",
};

/** 绑定字符串转成给人看的名字，比如 "KeyA" → "A"，"Shift+Slash" → "?"。 */
export function keyLabel(combo: string): string {
  if (combo === "Shift+Slash") return "?";
  const shift = combo.startsWith("Shift+");
  const code = shift ? combo.slice(6) : combo;
  let name = NAMES[code];
  if (!name) {
    if (code.startsWith("Key")) name = code.slice(3);
    else if (code.startsWith("Digit")) name = code.slice(5);
    else if (code.startsWith("Numpad")) name = "小键盘 " + code.slice(6);
    else name = code;
  }
  return shift ? `Shift+${name}` : name;
}

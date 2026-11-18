// 相册设置表单：发布对话框和相册详情页共用。

import type { Gallery, GalleryOpts } from "./api.js";
import { h } from "./util.js";

/** 时间戳 → <input type=date> 的值（本地日期）。 */
export function toDateInput(ts: number | null): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const pad = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** <input type=date> 的值 → 那一天结束时（本地 23:59:59）的时间戳。 */
export function fromDateInput(v: string): number | null {
  const m = v.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return null;
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 23, 59, 59).getTime() / 1000;
}

export function fmtDate(ts: number | null): string {
  return ts ? toDateInput(ts) : "";
}

function field(label: string, control: HTMLElement, hint?: string): HTMLElement {
  return h("label", { class: "field" },
    h("span", { class: "field-label", text: label }),
    control,
    hint ? h("span", { class: "field-hint muted small", text: hint }) : null);
}

export interface GalleryForm {
  el: HTMLElement;
  /** 读出表单；有问题时抛出 Error（中文说明）。 */
  read(): GalleryOpts;
}

export function galleryForm(opts: {
  creating: boolean;
  initial: Partial<Gallery>;
  watermarkText?: string;
}): GalleryForm {
  const g = opts.initial;
  const title = h("input", { class: "input", type: "text", maxlength: "100", value: g.title ?? "",
    name: "title" }) as HTMLInputElement;
  const client = h("input", { class: "input", type: "text", maxlength: "100", value: g.client_name ?? "",
    name: "client_name", placeholder: "可选" }) as HTMLInputElement;
  const message = h("textarea", { class: "input textarea", maxlength: "2000", rows: "3", name: "message",
    placeholder: "可选，会显示在客户页面顶部，比如：请选出 20 张，月底前提交。" }) as HTMLTextAreaElement;
  message.value = g.message ?? "";
  const lang = h("select", { class: "select", name: "lang" },
    h("option", { value: "auto", text: "跟随客户浏览器" }),
    h("option", { value: "zh", text: "中文" }),
    h("option", { value: "en", text: "English" })) as HTMLSelectElement;
  lang.value = g.lang ?? "auto";

  const limit = h("input", { class: "input narrow", type: "number", min: "1", step: "1", name: "limit_count",
    placeholder: "不限", value: g.limit ? String(g.limit) : "" }) as HTMLInputElement;
  const limitMode = h("select", { class: "select", name: "limit_mode" },
    h("option", { value: "block", text: "超出时不能提交" }),
    h("option", { value: "warn", text: "超出时提醒，仍可提交" })) as HTMLSelectElement;
  limitMode.value = g.limit_mode ?? "block";
  const syncLimit = () => { limitMode.disabled = !limit.value.trim(); };
  limit.addEventListener("input", syncLimit);
  syncLimit();

  const watermark = h("input", { type: "checkbox", name: "watermark", checked: g.watermark ?? true }) as HTMLInputElement;

  const radioName = `access-${Math.random().toString(36).slice(2)}`;
  const isPublic = h("input", { type: "radio", name: radioName, value: "public",
    checked: g.has_password === false || opts.creating }) as HTMLInputElement;
  const isPw = h("input", { type: "radio", name: radioName, value: "password",
    checked: g.has_password === true && !opts.creating }) as HTMLInputElement;
  const password = h("input", { class: "input", type: "text", maxlength: "100", name: "password",
    autocomplete: "off",
    placeholder: g.has_password ? "已设置密码；不修改请留空" : "客户打开链接时要输入的密码" }) as HTMLInputElement;
  const syncPw = () => { password.disabled = !isPw.checked; };
  isPublic.addEventListener("change", syncPw);
  isPw.addEventListener("change", syncPw);
  password.addEventListener("focus", () => { isPw.checked = true; syncPw(); });
  syncPw();

  const deadline = h("input", { class: "input narrow", type: "date", name: "deadline",
    value: toDateInput(g.deadline_at ?? null) }) as HTMLInputElement;
  const deleteAt = h("input", { class: "input narrow", type: "date", name: "delete_at",
    value: toDateInput(g.delete_at ?? null) }) as HTMLInputElement;

  const el = h("div", { class: "form" },
    field("标题", title),
    field("客户名", client),
    field("给客户的话", message),
    field("客户页面语言", lang),
    h("div", { class: "field" },
      h("span", { class: "field-label", text: "张数上限" }),
      h("div", { class: "row" }, limit, limitMode),
      h("span", { class: "field-hint muted small", text: "留空表示不限。客户选的时候不拦，提交时才检查。" })),
    opts.creating
      ? h("label", { class: "toggle-row" }, watermark,
        h("span", {}, `加水印：${opts.watermarkText ?? ""}`,
          h("span", { class: "muted small", text: "（水印文字在设置里改；发布后不能再改）" })))
      : h("div", { class: "field" },
        h("span", { class: "field-label", text: "水印" }),
        h("span", { text: g.watermark ? `有：${g.watermark_text}` : "无" })),
    h("div", { class: "field" },
      h("span", { class: "field-label", text: "访问方式" }),
      h("label", { class: "toggle-row" }, isPublic, h("span", { text: "公开：拿到链接就能看" })),
      h("label", { class: "toggle-row" }, isPw, h("span", { text: "需要密码" })),
      password),
    h("div", { class: "row two" },
      field("截止日期", deadline, "过了这一天，客户不能再改选择"),
      field("自动删除日期", deleteAt, "到期删除相册文件，链接失效；客户的选择和备注保留")),
  );

  function read(): GalleryOpts {
    const out: GalleryOpts = {};
    const t = title.value.trim();
    if (!t) throw new Error("请填写标题");
    out.title = t;
    out.client_name = client.value.trim();
    out.message = message.value.trim();
    out.lang = lang.value as Gallery["lang"];
    const lv = limit.value.trim();
    if (lv) {
      const n = Number(lv);
      if (!Number.isInteger(n) || n < 1) throw new Error("张数上限需要是正整数");
      out.limit_count = n;
    } else {
      out.limit_count = null;
    }
    out.limit_mode = limitMode.value as Gallery["limit_mode"];
    if (opts.creating) out.watermark = watermark.checked;
    if (isPublic.checked) {
      out.password = null;
    } else {
      const pw = password.value;
      if (pw) out.password = pw;
      else if (opts.creating || !g.has_password) throw new Error("请填写密码，或者选择公开");
    }
    out.deadline_at = deadline.value ? fromDateInput(deadline.value) : null;
    out.delete_at = deleteAt.value ? fromDateInput(deleteAt.value) : null;
    return out;
  }

  return { el, read };
}

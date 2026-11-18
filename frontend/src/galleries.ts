// 客户相册（管理员端）：相册列表、单个相册的详情（链接、客户选择、导出 RAW、交付成片、设置）。

import { api, galleryImg, type Gallery } from "./api.js";
import { openExportPanel } from "./exportPanel.js";
import { fmtDate, galleryForm } from "./galleryForm.js";
import { fmtBytes, fmtTime, h, openModal, toast } from "./util.js";

export function galleryStatus(g: Gallery): { key: string; text: string } {
  if (g.deleted_at) return { key: "deleted", text: "已删除" };
  if (g.status === "building") return { key: "building", text: g.build_error ? "生成失败" : "生成中" };
  if (g.status === "submitted") return { key: "submitted", text: "客户已提交" };
  if (g.locked === "deadline") return { key: "deadline", text: "已截止" };
  return { key: "open", text: "选片中" };
}

function statusBadge(g: Gallery): HTMLElement {
  const s = galleryStatus(g);
  return h("span", { class: `status gstatus-${s.key}`, text: s.text });
}

function selectedText(g: Gallery): string {
  const n = g.counts.selected;
  if (!g.limit) return `${n}`;
  return `${n} / ${g.limit}`;
}

async function copy(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    toast("已复制");
  } catch {
    // 非 https 的局域网地址上，部分浏览器不给用剪贴板接口
    const ta = h("textarea", { class: "offscreen" }) as HTMLTextAreaElement;
    ta.value = text;
    document.body.append(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    toast(ok ? "已复制" : "复制失败，请手动选中复制");
  }
}

// ================================================================== 列表

export class GalleriesView {
  constructor(private root: HTMLElement) {
    document.title = "客户相册 · rawpick";
    this.root.replaceChildren(
      h("header", { class: "topbar" },
        h("a", { class: "btn back", href: "#/", text: "‹ 返回" }),
        h("div", { class: "title", text: "客户相册" })),
      h("main", { class: "page" }, h("p", { class: "muted", text: "正在读取……" })));
    void this.load();
  }

  destroy(): void {}

  private async load(): Promise<void> {
    const main = this.root.querySelector("main")!;
    try {
      const { galleries } = await api.galleries();
      if (galleries.length === 0) {
        main.replaceChildren(h("p", { class: "muted",
          text: "还没有相册。在筛选界面里点“发布相册”，把已选的照片发给客户选。" }));
        return;
      }
      const head = h("tr", {}, ...["标题", "客户", "出门", "照片", "客户已选", "状态", "截止", "创建"]
        .map((t) => h("th", { text: t })));
      const rows = galleries.map((g) => {
        const tr = h("tr", { class: "row-link", "data-id": g.id },
          h("td", {}, h("a", { href: `#/g/${g.id}`, text: g.title })),
          h("td", { text: g.client_name || "—" }),
          h("td", { class: "muted", text: g.dir_name }),
          h("td", { class: "num", text: String(g.counts.total) }),
          h("td", { class: "num", text: selectedText(g) }),
          h("td", {}, statusBadge(g),
            g.delivery_status === "ready" ? h("span", { class: "status gstatus-delivered", text: "已交付" }) : null),
          h("td", { class: "nowrap", text: fmtDate(g.deadline_at) || "—" }),
          h("td", { class: "nowrap muted", text: fmtTime(g.created_at) }));
        tr.addEventListener("click", (e) => {
          if (!(e.target as HTMLElement).closest("a")) location.hash = `#/g/${g.id}`;
        });
        return tr;
      });
      main.replaceChildren(h("table", { class: "session-table" }, h("thead", {}, head), h("tbody", {}, ...rows)));
    } catch (e) {
      main.replaceChildren(h("p", { class: "error", text: `读取失败：${(e as Error).message}` }));
    }
  }
}

// ================================================================== 详情

export class GalleryView {
  private g: Gallery | null = null;
  private timer = 0;
  private onlySelected = true;
  private folders: { name: string; jpg_count: number }[] | null = null;
  private main = h("main", { class: "page gallery-page" });
  private titleEl = h("div", { class: "title" });
  private badgeEl = h("span", {});
  private openLink = h("a", { class: "btn", text: "打开客户页面", target: "_blank", rel: "noopener", hidden: true });

  constructor(private root: HTMLElement, private gid: number) {
    this.root.replaceChildren(
      h("header", { class: "topbar" },
        h("a", { class: "btn back", href: "#/galleries", text: "‹ 相册" }),
        this.titleEl, this.badgeEl,
        h("div", { class: "spacer" }),
        this.openLink),
      this.main);
    this.main.append(h("p", { class: "muted", text: "正在读取……" }));
    void this.load();
  }

  destroy(): void {
    window.clearTimeout(this.timer);
  }

  private async load(): Promise<void> {
    window.clearTimeout(this.timer);
    try {
      this.set(await api.gallery(this.gid));
    } catch (e) {
      this.main.replaceChildren(h("p", { class: "error", text: `读取失败：${(e as Error).message}` }));
    }
  }

  private set(g: Gallery): void {
    this.g = g;
    document.title = `${g.title} · 客户相册 · rawpick`;
    this.render();
    window.clearTimeout(this.timer);
    const busy = (g.status === "building" && !g.build_error) || g.delivery_status === "building";
    // 生成中时 1.5 秒刷新一次；平时 30 秒刷新一次，看客户的最新选择
    this.timer = window.setTimeout(() => void this.refresh(), busy ? 1500 : 30000);
  }

  private async refresh(): Promise<void> {
    // 正在编辑设置时不刷新整页，免得把输入冲掉
    if (this.main.contains(document.activeElement) && document.activeElement?.closest(".form")) {
      this.timer = window.setTimeout(() => void this.refresh(), 5000);
      return;
    }
    try {
      this.set(await api.gallery(this.gid));
    } catch {
      this.timer = window.setTimeout(() => void this.refresh(), 5000);
    }
  }

  private async act(fn: () => Promise<Gallery>, okText?: string): Promise<void> {
    try {
      this.set(await fn());
      if (okText) toast(okText);
    } catch (e) {
      toast((e as Error).message, 4000);
    }
  }

  private render(): void {
    const g = this.g!;
    this.titleEl.textContent = g.title;
    this.badgeEl.replaceChildren(statusBadge(g));
    this.openLink.hidden = !g.link || !!g.deleted_at;
    if (g.link) this.openLink.setAttribute("href", g.link);
    const scrollY = this.main.scrollTop;
    this.main.replaceChildren(
      this.linkSection(g),
      this.selectionSection(g),
      this.deliverySection(g),
      this.settingsSection(g),
      this.dangerSection(g));
    this.main.scrollTop = scrollY;
  }

  // ---------------------------------------------------------------- 链接
  private linkSection(g: Gallery): HTMLElement {
    const sec = h("section", { class: "settings-section" }, h("h2", { text: "客户链接" }));
    if (g.deleted_at) {
      sec.append(h("p", { class: "notice",
        text: `相册文件已于 ${fmtTime(g.deleted_at)} 删除，客户链接已失效。下面保留了客户的选择和备注。` }));
      return sec;
    }
    if (g.status === "building") {
      sec.append(g.build_error
        ? h("p", { class: "error", text: `生成失败：${g.build_error}` })
        : h("p", { text: `正在生成预览 ${g.counts.ready} / ${g.counts.total}，完成后客户才能打开。` }));
    } else if (g.build_error) {
      sec.append(h("p", { class: "warn", text: g.build_error }));
    }
    if (!g.link) {
      sec.append(h("p", { class: "muted", text: "客户端没有启动（CLIENT_PORT 为 0），没有可用的链接。" }));
      return sec;
    }
    const input = h("input", { class: "input link-input", readonly: true, value: g.link,
      "data-testid": "gallery-link" }) as HTMLInputElement;
    input.addEventListener("focus", () => input.select());
    sec.append(
      h("div", { class: "row" }, input,
        h("button", { class: "btn", text: "复制", onclick: () => void copy(g.link!) }),
        h("button", { class: "btn", text: "重置链接", onclick: () => this.confirmResetLink() })),
      h("p", { class: "muted small",
        text: (g.has_password ? "需要密码才能打开。" : "公开：拿到链接就能看。")
          + " 链接地址在“设置 → 客户相册”里配置。" }));
    if (g.link_uncertain) {
      sec.append(h("p", { class: "notice", "data-testid": "link-warning" },
        `管理员端的端口被映射过（现在是 ${location.port}），链接里的客户端端口是容器内的端口，可能不对。`,
        h("a", { href: "#/settings", text: "去设置客户访问地址" })));
    }
    return sec;
  }

  private confirmResetLink(): void {
    const m = openModal("重置链接", () => {});
    m.body.append(h("p", { text: "生成一个新链接，旧链接马上失效。客户的选择不受影响，但要把新链接重新发给客户。" }));
    m.actions.append(
      h("button", { class: "btn", text: "取消", onclick: () => m.close() }),
      h("button", { class: "btn primary", text: "重置", onclick: () => {
        m.close();
        void this.act(() => api.resetLink(this.gid), "已生成新链接");
      } }));
  }

  // ---------------------------------------------------------------- 客户选择
  private selectionSection(g: Gallery): HTMLElement {
    const c = g.counts;
    const sec = h("section", { class: "settings-section" }, h("h2", { text: "客户选择" }));
    const parts = [`共 ${c.ready} 张，客户已选 ${c.selected} 张`];
    if (g.limit) {
      parts.push(`上限 ${g.limit} 张（${g.limit_mode === "block" ? "超出不能提交" : "超出提醒后可提交"}）`);
    }
    if (c.notes) parts.push(`${c.notes} 张有备注`);
    sec.append(h("p", { "data-testid": "gallery-summary", text: parts.join(" · ") }));
    if (g.status === "submitted") {
      sec.append(h("p", { class: "ok", text: `客户已于 ${fmtTime(g.submitted_at)} 提交。` }));
    } else if (g.locked === "deadline") {
      sec.append(h("p", { class: "warn", text: "已过截止日期，客户不能再修改。可以在下面的设置里改截止日期。" }));
    }

    const dest = h("input", { class: "input", value: g.export_dir, "aria-label": "导出文件夹",
      "data-testid": "export-dir" }) as HTMLInputElement;
    const actions = h("div", { class: "row wrap" });
    if (g.status === "submitted") {
      actions.append(h("button", { class: "btn", text: "重新开放选片",
        onclick: () => void this.act(() => api.reopenGallery(this.gid), "客户可以继续修改了") }));
    }
    actions.append(
      h("span", { class: "muted small", text: "导出到出门目录下：" }), dest,
      h("button", { class: "btn primary", text: "导出客户所选 RAW", "data-testid": "gallery-export",
        disabled: c.selected === 0 || g.session_id === null,
        onclick: () => this.openExport(dest.value.trim()) }));
    sec.append(actions);
    if (g.last_export_at) {
      sec.append(h("p", { class: "muted small", text: `上次导出 ${fmtTime(g.last_export_at)}` }));
    }

    const photos = (g.photos ?? []).filter((p) => !this.onlySelected || p.selected);
    const toggle = h("label", { class: "toggle-row" },
      h("input", { type: "checkbox", checked: this.onlySelected, onchange: (e: Event) => {
        this.onlySelected = (e.target as HTMLInputElement).checked;
        this.render();
      } }),
      h("span", { text: "只看客户已选的" }));
    sec.append(toggle);
    if (photos.length === 0) {
      sec.append(h("p", { class: "muted", text: this.onlySelected ? "客户还没有选照片。" : "没有照片。" }));
      return sec;
    }
    const list = h("div", { class: "gphotos" });
    for (const p of photos) {
      list.append(h("div", { class: p.selected ? "gphoto selected" : "gphoto" },
        g.deleted_at || !p.ready
          ? h("div", { class: "gthumb missing", text: p.error ? "生成失败" : "" })
          : h("img", { class: "gthumb", src: galleryImg(g.id, p.id, "thumb"), loading: "lazy", alt: "" }),
        h("div", { class: "gphoto-info" },
          h("div", { class: "gphoto-name" }, p.selected ? "♥ " : "", p.name,
            p.raw ? null : h("span", { class: "warn small", text: " · 缺 RAW" })),
          p.note ? h("div", { class: "gphoto-note", text: p.note }) : null)));
    }
    sec.append(list);
    return sec;
  }

  private openExport(dest: string): void {
    if (!dest) {
      toast("请填写导出文件夹名");
      return;
    }
    openExportPanel({
      title: `导出客户所选 RAW 到 ${dest}/`,
      dest,
      plan: () => api.galleryPlan(this.gid, dest),
      start: () => api.galleryExport(this.gid, dest),
    }, null, () => void this.load());
  }

  // ---------------------------------------------------------------- 交付
  private deliverySection(g: Gallery): HTMLElement {
    const sec = h("section", { class: "settings-section" }, h("h2", { text: "交付成片" }));
    if (g.deleted_at) {
      sec.append(h("p", { class: "muted", text: "相册文件已删除。" }));
      return sec;
    }
    const n = (g.delivery ?? []).filter((d) => d.ready).length;
    const size = (g.delivery ?? []).reduce((s, d) => s + d.size, 0);
    if (g.delivery_status === "building") {
      sec.append(h("p", { text: `正在导入 ${g.delivery_dir}/ 里的成片……已完成 ${n} 张` }));
      return sec;
    }
    if (g.delivery_status === "ready") {
      sec.append(h("p", { class: "ok", "data-testid": "delivery-summary",
        text: `已交付 ${g.delivery_dir}/ 里的 ${n} 张成片（${fmtBytes(size)}），客户可以在链接里下载。` }));
    } else if (g.delivery_status === "failed") {
      sec.append(h("p", { class: "error", text: `交付失败：${g.delivery_error}` }));
    } else {
      sec.append(h("p", { class: "muted",
        text: "修好图后，把成片 JPG 导出到这次出门目录下的一个文件夹（比如 final/），在这里选中它发布给客户下载。成片是原图，不加水印。" }));
    }
    const sel = h("select", { class: "select", "aria-label": "成片文件夹", "data-testid": "delivery-folder" }) as HTMLSelectElement;
    const btn = h("button", { class: "btn primary", text: g.delivery_status === "ready" ? "重新交付" : "发布成片",
      "data-testid": "deliver-btn" }) as HTMLButtonElement;
    const fill = () => {
      const folders = this.folders ?? [];
      sel.replaceChildren(...folders.map((f) => h("option", {
        value: f.name, text: `${f.name}/（${f.jpg_count} 张 JPG）`, disabled: f.jpg_count === 0,
        selected: f.name === g.delivery_dir,
      })));
      if (folders.length === 0) sel.append(h("option", { value: "", text: "没有可用的文件夹" }));
      btn.disabled = !folders.some((f) => f.jpg_count > 0);
    };
    if (this.folders === null) {
      sel.append(h("option", { text: "正在读取文件夹……" }));
      btn.disabled = true;
      api.deliveryFolders(this.gid).then((r) => { this.folders = r.folders; fill(); },
        (e) => { sel.replaceChildren(h("option", { text: (e as Error).message })); });
    } else {
      fill();
    }
    btn.addEventListener("click", () => {
      if (!sel.value) return;
      const go = () => void this.act(() => api.deliver(this.gid, sel.value));
      if (g.delivery_status === "ready") {
        const m = openModal("重新交付", () => {});
        m.body.append(h("p", { text: `用 ${sel.value}/ 里的成片替换现在交付的 ${n} 张。` }));
        m.actions.append(h("button", { class: "btn", text: "取消", onclick: () => m.close() }),
          h("button", { class: "btn primary", text: "替换", onclick: () => { m.close(); go(); } }));
      } else {
        go();
      }
    });
    const refresh = h("button", { class: "btn", text: "重新读取文件夹", onclick: () => {
      this.folders = null;
      this.render();
    } });
    sec.append(h("div", { class: "row wrap" }, sel, btn, refresh));
    if (g.delivery_status === "ready" || g.delivery_status === "failed") {
      sec.append(h("div", { class: "row" }, h("button", { class: "btn danger-btn", text: "撤回交付",
        onclick: () => void this.act(() => api.cancelDelivery(this.gid), "已撤回，客户看不到成片了") })));
    }
    return sec;
  }

  // ---------------------------------------------------------------- 设置
  private settingsSection(g: Gallery): HTMLElement {
    const form = galleryForm({ creating: false, initial: g });
    const err = h("p", { class: "error", hidden: true });
    const save = h("button", { class: "btn primary", text: "保存设置", "data-testid": "gallery-save" });
    save.addEventListener("click", () => {
      err.hidden = true;
      let opts;
      try {
        opts = form.read();
      } catch (e) {
        err.textContent = (e as Error).message;
        err.hidden = false;
        return;
      }
      (document.activeElement as HTMLElement | null)?.blur();
      void this.act(() => api.updateGallery(this.gid, opts), "已保存");
    });
    return h("section", { class: "settings-section" },
      h("h2", { text: "设置" }),
      h("p", { class: "muted small", text: `来自 ${g.dir_name} · 编号 ${g.code} · 创建于 ${fmtTime(g.created_at)}` }),
      form.el, err, h("div", { class: "settings-actions" }, save));
  }

  private dangerSection(g: Gallery): HTMLElement {
    return h("section", { class: "settings-section" },
      h("h2", { text: "删除相册" }),
      h("p", { class: "muted small",
        text: "删除相册文件和这条记录（包括客户的选择和备注），客户链接失效。照片目录里的文件不受影响。" }),
      h("button", { class: "btn danger-btn", text: "删除相册", onclick: () => {
        const m = openModal("删除相册", () => {});
        m.body.append(h("p", { text: `确定删除“${g.title}”吗？客户的选择和备注也会一起删除，不能恢复。` }));
        m.actions.append(h("button", { class: "btn", text: "取消", onclick: () => m.close() }),
          h("button", { class: "btn danger-btn", text: "删除", onclick: async () => {
            m.close();
            try {
              await api.deleteGallery(this.gid);
              toast("已删除");
              location.hash = "#/galleries";
            } catch (e) {
              toast((e as Error).message, 4000);
            }
          } }));
      } }));
  }
}

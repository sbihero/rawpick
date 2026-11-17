// 设置页里的“找回标记”：自动关联开关；为找不到文件夹的旧记录手动指定新文件夹，或删除记录。

import { api, type Orphan } from "./api.js";
import { fmtTime, h, toast } from "./util.js";

function item(o: Orphan, reload: () => void): HTMLElement {
  const sel = h("select", { class: "select", "aria-label": `关联 ${o.name} 到` }) as HTMLSelectElement;
  sel.append(h("option", { value: "", text: "选择要关联的文件夹……" }));
  for (const c of o.candidates) {
    sel.append(h("option", { value: c.name,
      text: `${c.name}（同名照片 ${c.overlap}/${o.photos}，现有 ${c.count} 张）` }));
  }
  const adoptBtn = h("button", { class: "btn primary", text: "关联", disabled: true }) as HTMLButtonElement;
  sel.addEventListener("change", () => { adoptBtn.disabled = !sel.value; });
  adoptBtn.addEventListener("click", async () => {
    const target = sel.value;
    const c = o.candidates.find((x) => x.name === target);
    if (!c) return;
    if (c.overlap < o.photos &&
        !confirm(`“${target}”和原记录只有 ${c.overlap}/${o.photos} 张照片同名。\n` +
          "同名照片保留标记，其余照片的标记会丢弃。确定关联吗？")) return;
    adoptBtn.disabled = true;
    try {
      await api.adopt(o.id, target);
      toast(`已关联：${o.name} → ${target}`, 4000);
      reload();
    } catch (e) {
      toast(`关联失败：${(e as Error).message}`, 5000);
      adoptBtn.disabled = false;
    }
  });

  const delBtn = h("button", { class: "btn danger-btn", text: "删除这条记录" }) as HTMLButtonElement;
  delBtn.addEventListener("click", async () => {
    if (!confirm(`删除“${o.name}”的记录？\n` +
        `它的 ${o.picked} 个选取标记和导出记录会被删除，不能恢复。磁盘上的照片和 select/ 里的文件不受影响。`)) return;
    delBtn.disabled = true;
    try {
      await api.deleteOrphan(o.id);
      toast(`已删除“${o.name}”的记录`, 3000);
      reload();
    } catch (e) {
      toast(`删除失败：${(e as Error).message}`, 5000);
      delBtn.disabled = false;
    }
  });

  const meta = `${o.photos} 张照片，已选 ${o.picked} 张` +
    (o.last_export_at ? `，上次导出 ${fmtTime(o.last_export_at)}` : "");
  return h("div", { class: "orphan", "data-name": o.name },
    h("div", { class: "orphan-name", text: o.name }),
    h("div", { class: "muted small", text: meta }),
    h("div", { class: "orphan-actions" },
      o.candidates.length
        ? sel
        : h("span", { class: "muted small", text: "没有可以关联的空白文件夹（磁盘上的文件夹都已经有标记）。" }),
      o.candidates.length ? adoptBtn : null,
      delBtn));
}

function autoToggle(): HTMLElement {
  const box = h("input", { type: "checkbox", id: "auto-adopt" }) as HTMLInputElement;
  box.disabled = true;
  api.settings().then((s) => {
    box.checked = s.auto_adopt !== false;
    box.disabled = false;
  }, () => {});
  box.addEventListener("change", async () => {
    box.disabled = true;
    try {
      await api.saveSettings({ auto_adopt: box.checked });
      toast(box.checked ? "已打开自动关联" : "已关闭自动关联");
    } catch (e) {
      box.checked = !box.checked;
      toast(`保存失败：${(e as Error).message}`, 4000);
    } finally {
      box.disabled = false;
    }
  });
  return h("label", { class: "toggle-row", for: "auto-adopt" }, box,
    h("span", {}, "自动关联",
      h("span", { class: "muted small",
        text: "：文件夹改名后，如果新文件夹里的照片和旧记录完全一样，刷新首页时自动把标记接过去" })));
}

export async function renderOrphans(box: HTMLElement): Promise<void> {
  const list = h("div", {}, h("p", { class: "muted small", text: "正在检查……" }));
  box.replaceChildren(autoToggle(), list);
  const reload = () => void renderOrphans(box);
  try {
    const { orphans } = await api.orphans();
    if (orphans.length === 0) {
      list.replaceChildren(h("p", { class: "muted", text: "没有需要处理的记录：所有标记都能对应到现有的文件夹。" }));
      return;
    }
    list.replaceChildren(
      h("p", { class: "small",
        text: `下面 ${orphans.length} 条记录找不到对应的文件夹。选一个文件夹关联，标记就会转过去；文件夹已经删掉的，可以删除记录。` }),
      ...orphans.map((o) => item(o, reload)));
  } catch (e) {
    list.replaceChildren(h("p", { class: "error", text: `读取失败：${(e as Error).message}` }));
  }
}

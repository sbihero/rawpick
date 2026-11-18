// 导出面板：先看清单 → 确认 → 后台拷贝并显示进度 → 结果。
// 粗筛导出到 select/，客户相册导出到 client_.../，共用这个面板。
import { api } from "./api.js";
import { fmtBytes, h, nameList, openModal } from "./util.js";
function row(label, value, names, note) {
    const head = h("div", { class: "plan-row" }, h("span", { class: "plan-label", text: label }), h("span", { class: "plan-value", text: value }));
    if (!names || names.length === 0)
        return head;
    return h("details", { class: "plan-details" }, h("summary", {}, head), note ? h("p", { class: "muted small", text: note }) : null, nameList(names));
}
export function openExportPanel(target, runningJob, onClose) {
    let timer = 0;
    const d = `${target.dest}/`;
    const m = openModal(target.title, () => {
        window.clearTimeout(timer);
        onClose();
    });
    m.el.dataset.testid = "export-panel";
    const closeBtn = h("button", { class: "btn", text: "关闭", onclick: () => m.close() });
    function showPlan(plan) {
        m.body.replaceChildren(row("已选照片", `${plan.picked} 张`), row("将拷贝", `${plan.copy.length} 张 · ${fmtBytes(plan.copy_bytes)}`, plan.copy), row(`已在 ${d} 中，跳过`, `${plan.skip.length} 张`, plan.skip), row("同名但大小不同，不覆盖", `${plan.conflict.length} 张`, plan.conflict), row("已选但缺少 RAW", `${plan.missing_raw.length} 张`, plan.missing_raw), row(`${d} 中有、但现在未选`, `${plan.stale.length} 个`, plan.stale, "这些文件不会被删除，如需清理请自己处理。"));
        const start = h("button", { class: "btn primary", text: "开始导出" });
        if (plan.copy.length === 0) {
            start.disabled = true;
            start.textContent = "没有需要拷贝的文件";
        }
        start.addEventListener("click", async () => {
            start.disabled = true;
            try {
                const { job_id } = await target.start();
                poll(job_id);
            }
            catch (e) {
                const data = e.data;
                if (data && typeof data.job_id === "number")
                    poll(data.job_id);
                else
                    showError(e.message);
            }
        });
        m.actions.replaceChildren(h("button", { class: "btn", text: "取消", onclick: () => m.close() }), start);
    }
    function showProgress(job) {
        const pct = job.bytes_total ? Math.round((job.bytes_done / job.bytes_total) * 100) : 100;
        const bar = h("div", { class: "bar" }, h("div", { class: "bar-fill", style: `width:${pct}%` }));
        m.body.replaceChildren(h("p", { text: `正在拷贝 ${job.done} / ${job.total} 张` }), bar, h("p", { class: "muted", text: `${fmtBytes(job.bytes_done)} / ${fmtBytes(job.bytes_total)} · 关闭面板不会中断拷贝` }));
        m.actions.replaceChildren(closeBtn);
    }
    function showResult(job) {
        const r = job.report;
        if (job.status === "failed" || !r) {
            showError(job.error || "导出失败");
            return;
        }
        const errs = r.errors.map((e) => `${e.name}：${e.error}`);
        m.body.replaceChildren(h("p", { class: r.errors.length ? "warn" : "ok",
            text: r.errors.length ? "导出完成，但有文件出错" : "导出完成" }), row("已拷贝", `${r.copied.length} 张`, r.copied), row("已存在，跳过", `${r.skipped.length} 张`, r.skipped), row("同名但大小不同，未覆盖", `${r.conflicts.length} 张`, r.conflicts), row("缺少 RAW", `${r.missing_raw.length} 张`, r.missing_raw), row("出错", `${r.errors.length} 张`, errs), row(`${d} 中有、但现在未选`, `${r.stale.length} 个`, r.stale, "这些文件没有被删除。"));
        m.actions.replaceChildren(closeBtn);
    }
    function showError(msg) {
        m.body.replaceChildren(h("p", { class: "error", text: msg }));
        m.actions.replaceChildren(closeBtn);
    }
    async function poll(jobId) {
        try {
            const job = await api.job(jobId);
            if (job.status === "running") {
                showProgress(job);
                timer = window.setTimeout(() => poll(jobId), 500);
            }
            else {
                showResult(job);
            }
        }
        catch (e) {
            showError(e.message);
        }
    }
    m.body.replaceChildren(h("p", { class: "muted", text: "正在计算导出清单……" }));
    if (runningJob !== null) {
        poll(runningJob);
    }
    else {
        target.plan().then(showPlan, (e) => showError(e.message));
    }
}

// 发布相册：把这次出门的已选照片发布给客户选片。
import { api } from "./api.js";
import { galleryForm } from "./galleryForm.js";
import { parseName } from "./home.js";
import { h, openModal, toast } from "./util.js";
export function openPublishDialog(session, picked, onClose) {
    const m = openModal("发布相册给客户", onClose);
    m.el.dataset.testid = "publish-dialog";
    if (picked === 0) {
        m.body.append(h("p", { text: "这次出门还没有已选的照片。先粗筛，再把已选的照片发布给客户。" }));
        m.actions.append(h("button", { class: "btn", text: "关闭", onclick: () => m.close() }));
        return;
    }
    m.body.append(h("p", { class: "muted", text: "正在读取设置……" }));
    void api.settings().then((s) => {
        const form = galleryForm({
            creating: true,
            initial: { title: parseName(session.name).topic, limit_mode: "block", watermark: true },
            watermarkText: s.watermark_text || "严记肘子铺",
        });
        const err = h("p", { class: "error", hidden: true });
        m.body.replaceChildren(h("p", { text: `将发布这次出门的 ${picked} 张已选照片。客户看到的是加了水印的 2048px 预览，看不到原图。` }), form.el, err);
        const submit = h("button", { class: "btn primary", text: "发布", "data-testid": "publish-submit" });
        submit.addEventListener("click", async () => {
            err.hidden = true;
            let opts;
            try {
                opts = form.read();
            }
            catch (e) {
                err.textContent = e.message;
                err.hidden = false;
                return;
            }
            submit.disabled = true;
            try {
                const g = await api.createGallery(session.id, opts);
                m.close();
                toast("相册已创建，正在生成预览");
                location.hash = `#/g/${g.id}`;
            }
            catch (e) {
                err.textContent = e.message;
                err.hidden = false;
                submit.disabled = false;
            }
        });
        m.actions.replaceChildren(h("button", { class: "btn", text: "取消", onclick: () => m.close() }), submit);
    }, (e) => {
        m.body.replaceChildren(h("p", { class: "error", text: e.message }));
    });
}

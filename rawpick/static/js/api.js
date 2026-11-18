// 后端接口。
export class ApiError extends Error {
    status;
    data;
    constructor(message, status, data) {
        super(message);
        this.status = status;
        this.data = data;
    }
}
async function req(method, url, body, keepalive = false) {
    const r = await fetch(url, {
        method,
        keepalive,
        headers: body === undefined ? {} : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok)
        throw new ApiError((data && data.error) || `HTTP ${r.status}`, r.status, data);
    return data;
}
export const api = {
    health: () => req("GET", "/api/health"),
    settings: () => req("GET", "/api/settings"),
    saveSettings: (patch) => req("PUT", "/api/settings", patch),
    sessions: () => req("GET", "/api/sessions"),
    orphans: () => req("GET", "/api/orphans"),
    adopt: (sid, target) => req("POST", `/api/orphans/${sid}/adopt`, { target }),
    deleteOrphan: (sid) => req("DELETE", `/api/orphans/${sid}`),
    open: (sid) => req("POST", `/api/sessions/${sid}/open`),
    photos: (sid) => req("GET", `/api/sessions/${sid}/photos`),
    issues: (sid) => req("GET", `/api/sessions/${sid}/issues`),
    status: (sid) => req("GET", `/api/sessions/${sid}/status`),
    /** keepalive：页面关闭或刷新时也能把请求发出去 */
    cursor: (sid, photoId, keepalive = false) => req("PUT", `/api/sessions/${sid}/cursor`, { photo_id: photoId }, keepalive),
    photo: (pid) => req("GET", `/api/photos/${pid}`),
    setPicked: (pid, picked) => req("PUT", `/api/photos/${pid}/picked`, { picked }),
    plan: (sid) => req("GET", `/api/sessions/${sid}/export/plan`),
    startExport: (sid) => req("POST", `/api/sessions/${sid}/export`),
    job: (jid) => req("GET", `/api/jobs/${jid}`),
    galleries: () => req("GET", "/api/galleries"),
    createGallery: (sid, opts) => req("POST", `/api/sessions/${sid}/galleries`, opts),
    gallery: (gid) => req("GET", `/api/galleries/${gid}`),
    updateGallery: (gid, opts) => req("PUT", `/api/galleries/${gid}`, opts),
    deleteGallery: (gid) => req("DELETE", `/api/galleries/${gid}`),
    reopenGallery: (gid) => req("POST", `/api/galleries/${gid}/reopen`),
    resetLink: (gid) => req("POST", `/api/galleries/${gid}/reset-link`),
    galleryPlan: (gid, dest) => req("GET", `/api/galleries/${gid}/export/plan?dest=${encodeURIComponent(dest)}`),
    galleryExport: (gid, dest) => req("POST", `/api/galleries/${gid}/export`, { dest }),
    deliveryFolders: (gid) => req("GET", `/api/galleries/${gid}/delivery/folders`),
    deliver: (gid, folder) => req("POST", `/api/galleries/${gid}/delivery`, { folder }),
    cancelDelivery: (gid) => req("DELETE", `/api/galleries/${gid}/delivery`),
};
export function galleryImg(gid, pid, kind) {
    return `/api/galleries/${gid}/img/${pid}/${kind}`;
}
export function imgUrl(p, kind) {
    return `/img/${p.id}/${kind}?v=${p.v}`;
}

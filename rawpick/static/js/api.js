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
async function req(method, url, body) {
    const r = await fetch(url, {
        method,
        headers: body === undefined ? {} : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok)
        throw new ApiError((data && data.error) || `HTTP ${r.status}`, r.status, data);
    return data;
}
export const api = {
    sessions: () => req("GET", "/api/sessions"),
    open: (sid) => req("POST", `/api/sessions/${sid}/open`),
    photos: (sid) => req("GET", `/api/sessions/${sid}/photos`),
    issues: (sid) => req("GET", `/api/sessions/${sid}/issues`),
    status: (sid) => req("GET", `/api/sessions/${sid}/status`),
    cursor: (sid, photoId) => req("PUT", `/api/sessions/${sid}/cursor`, { photo_id: photoId }),
    photo: (pid) => req("GET", `/api/photos/${pid}`),
    setPicked: (pid, picked) => req("PUT", `/api/photos/${pid}/picked`, { picked }),
    plan: (sid) => req("GET", `/api/sessions/${sid}/export/plan`),
    startExport: (sid) => req("POST", `/api/sessions/${sid}/export`),
    job: (jid) => req("GET", `/api/jobs/${jid}`),
};
export function imgUrl(p, kind) {
    return `/img/${p.id}/${kind}?v=${p.v}`;
}

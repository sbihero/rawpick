// 后端接口。

export interface Exif {
  shutter?: string;
  aperture?: string;
  iso?: string;
  focal?: string;
  taken?: string;
}

export interface Photo {
  id: number;
  name: string;
  raw: string | null;
  picked: boolean;
  v: string;
  exif: Exif | null;
  error: string | null;
}

export interface Session {
  id: number;
  name: string;
  scanned: boolean;
  total: number;
  picked: number;
  last_export_at: number | null;
  cursor: number | null;
}

export interface Issue {
  kind: "raw_only" | "conflict" | "subdir";
  name: string;
}

export interface Plan {
  picked: number;
  copy: string[];
  skip: string[];
  conflict: string[];
  missing_raw: string[];
  stale: string[];
  copy_bytes: number;
}

export interface JobReport {
  copied: string[];
  skipped: string[];
  conflicts: string[];
  missing_raw: string[];
  stale: string[];
  errors: { name: string; error: string }[];
}

export interface Job {
  id: number;
  status: "running" | "done" | "failed";
  total: number;
  done: number;
  bytes_total: number;
  bytes_done: number;
  report: JobReport | null;
  error: string | null;
}

export interface Status {
  preview: { total: number; ready: number; failed: number };
  export_job: number | null;
  picked: number;
}

export class ApiError extends Error {
  constructor(message: string, public status: number, public data: Record<string, unknown>) {
    super(message);
  }
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError((data && data.error) || `HTTP ${r.status}`, r.status, data);
  return data as T;
}

export const api = {
  sessions: () => req<{ root: string; sessions: Session[] }>("GET", "/api/sessions"),
  open: (sid: number) =>
    req<{ session: Session; scan: { photos: number; added: number; removed: number; issues: number } }>(
      "POST", `/api/sessions/${sid}/open`),
  photos: (sid: number) => req<{ session: Session; photos: Photo[] }>("GET", `/api/sessions/${sid}/photos`),
  issues: (sid: number) => req<{ issues: Issue[] }>("GET", `/api/sessions/${sid}/issues`),
  status: (sid: number) => req<Status>("GET", `/api/sessions/${sid}/status`),
  cursor: (sid: number, photoId: number) =>
    req<{ ok: boolean }>("PUT", `/api/sessions/${sid}/cursor`, { photo_id: photoId }),
  photo: (pid: number) => req<Photo>("GET", `/api/photos/${pid}`),
  setPicked: (pid: number, picked: boolean) =>
    req<{ id: number; picked: boolean; picked_count: number }>("PUT", `/api/photos/${pid}/picked`, { picked }),
  plan: (sid: number) => req<Plan>("GET", `/api/sessions/${sid}/export/plan`),
  startExport: (sid: number) => req<{ job_id: number }>("POST", `/api/sessions/${sid}/export`),
  job: (jid: number) => req<Job>("GET", `/api/jobs/${jid}`),
};

export function imgUrl(p: Photo, kind: "thumb" | "preview" | "original"): string {
  return `/img/${p.id}/${kind}?v=${p.v}`;
}

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
  select_count: number;
  /** new：没打开过；opened：打开过、select/ 里还没有文件；exported：select/ 里有文件 */
  status: "new" | "opened" | "exported";
  last_export_at: number | null;
  cursor: number | null;
}

export interface Settings {
  shortcuts?: Record<string, string[]>;
  auto_adopt?: boolean;
  watermark_text?: string;
  client_base_url?: string;
}

export interface Orphan {
  id: number;
  name: string;
  photos: number;
  picked: number;
  last_export_at: number | null;
  candidates: { name: string; overlap: number; count: number }[];
}

export interface Health {
  ok: boolean;
  version: string;
  photo_root: string;
  photo_root_exists: boolean;
  data_dir: string;
  client_port: number;
  watermark_font: string;
}

export interface Issue {
  kind: "raw_only" | "conflict" | "subdir";
  name: string;
}

export interface Plan {
  dest: string;
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

export interface GalleryPhoto {
  id: number;
  name: string;
  raw: string | null;
  ready: boolean;
  error: string | null;
  selected: boolean;
  note: string;
}

export interface Gallery {
  id: number;
  code: string;
  title: string;
  client_name: string;
  message: string;
  lang: "auto" | "zh" | "en";
  limit: number | null;
  limit_mode: "block" | "warn";
  watermark: boolean;
  watermark_text: string;
  has_password: boolean;
  deadline_at: number | null;
  delete_at: number | null;
  /** building：正在生成；open：客户可以选；submitted：客户已提交 */
  status: "building" | "open" | "submitted";
  locked: "submitted" | "deadline" | null;
  build_error: string | null;
  created_at: number;
  published_at: number | null;
  submitted_at: number | null;
  session_id: number | null;
  dir_name: string;
  export_dir: string;
  last_export_at: number | null;
  delivery_dir: string | null;
  delivery_status: "building" | "ready" | "failed" | null;
  delivery_error: string | null;
  delivered_at: number | null;
  deleted_at: number | null;
  counts: { total: number; ready: number; failed: number; selected: number; notes: number };
  link: string | null;
  /** 链接是自动拼的，而端口映射过，可能不对 */
  link_uncertain: boolean;
  photos?: GalleryPhoto[];
  delivery?: { id: number; name: string; size: number; ready: number }[];
}

/** 发布和修改相册时可以设置的项。password：字符串设置新密码，null 表示公开，不传表示不变。 */
export interface GalleryOpts {
  title?: string;
  client_name?: string;
  message?: string;
  lang?: Gallery["lang"];
  limit_count?: number | null;
  limit_mode?: Gallery["limit_mode"];
  watermark?: boolean;
  password?: string | null;
  deadline_at?: number | null;
  delete_at?: number | null;
}

export class ApiError extends Error {
  constructor(message: string, public status: number, public data: Record<string, unknown>) {
    super(message);
  }
}

async function req<T>(method: string, url: string, body?: unknown, keepalive = false): Promise<T> {
  const r = await fetch(url, {
    method,
    keepalive,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError((data && data.error) || `HTTP ${r.status}`, r.status, data);
  return data as T;
}

export const api = {
  health: () => req<Health>("GET", "/api/health"),
  settings: () => req<Settings>("GET", "/api/settings"),
  saveSettings: (patch: Record<string, unknown>) => req<Settings>("PUT", "/api/settings", patch),
  sessions: () =>
    req<{ root: string; sessions: Session[]; adopted?: { from: string; to: string }[]; unmatched?: number }>(
      "GET", "/api/sessions"),
  orphans: () => req<{ orphans: Orphan[] }>("GET", "/api/orphans"),
  adopt: (sid: number, target: string) =>
    req<{ ok: boolean }>("POST", `/api/orphans/${sid}/adopt`, { target }),
  deleteOrphan: (sid: number) => req<{ ok: boolean }>("DELETE", `/api/orphans/${sid}`),
  open: (sid: number) =>
    req<{ session: Session; scan: { photos: number; added: number; removed: number; issues: number } }>(
      "POST", `/api/sessions/${sid}/open`),
  photos: (sid: number) => req<{ session: Session; photos: Photo[] }>("GET", `/api/sessions/${sid}/photos`),
  issues: (sid: number) => req<{ issues: Issue[] }>("GET", `/api/sessions/${sid}/issues`),
  status: (sid: number) => req<Status>("GET", `/api/sessions/${sid}/status`),
  /** keepalive：页面关闭或刷新时也能把请求发出去 */
  cursor: (sid: number, photoId: number, keepalive = false) =>
    req<{ ok: boolean }>("PUT", `/api/sessions/${sid}/cursor`, { photo_id: photoId }, keepalive),
  photo: (pid: number) => req<Photo>("GET", `/api/photos/${pid}`),
  setPicked: (pid: number, picked: boolean) =>
    req<{ id: number; picked: boolean; picked_count: number }>("PUT", `/api/photos/${pid}/picked`, { picked }),
  plan: (sid: number) => req<Plan>("GET", `/api/sessions/${sid}/export/plan`),
  startExport: (sid: number) => req<{ job_id: number }>("POST", `/api/sessions/${sid}/export`),
  job: (jid: number) => req<Job>("GET", `/api/jobs/${jid}`),
  galleries: () => req<{ galleries: Gallery[] }>("GET", "/api/galleries"),
  createGallery: (sid: number, opts: GalleryOpts) => req<Gallery>("POST", `/api/sessions/${sid}/galleries`, opts),
  gallery: (gid: number) => req<Gallery>("GET", `/api/galleries/${gid}`),
  updateGallery: (gid: number, opts: GalleryOpts) => req<Gallery>("PUT", `/api/galleries/${gid}`, opts),
  deleteGallery: (gid: number) => req<{ ok: boolean }>("DELETE", `/api/galleries/${gid}`),
  reopenGallery: (gid: number) => req<Gallery>("POST", `/api/galleries/${gid}/reopen`),
  resetLink: (gid: number) => req<Gallery>("POST", `/api/galleries/${gid}/reset-link`),
  galleryPlan: (gid: number, dest: string) =>
    req<Plan>("GET", `/api/galleries/${gid}/export/plan?dest=${encodeURIComponent(dest)}`),
  galleryExport: (gid: number, dest: string) =>
    req<{ job_id: number }>("POST", `/api/galleries/${gid}/export`, { dest }),
  deliveryFolders: (gid: number) =>
    req<{ folders: { name: string; jpg_count: number }[] }>("GET", `/api/galleries/${gid}/delivery/folders`),
  deliver: (gid: number, folder: string) => req<Gallery>("POST", `/api/galleries/${gid}/delivery`, { folder }),
  cancelDelivery: (gid: number) => req<Gallery>("DELETE", `/api/galleries/${gid}/delivery`),
};

export function galleryImg(gid: number, pid: number, kind: "thumb" | "preview"): string {
  return `/api/galleries/${gid}/img/${pid}/${kind}`;
}

export function imgUrl(p: Photo, kind: "thumb" | "preview" | "original" | "download"): string {
  return `/img/${p.id}/${kind}?v=${p.v}`;
}

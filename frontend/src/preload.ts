// 预加载窗口：提前下载并解码前后几张大预览，移出窗口的立即释放。

import { imgUrl, type Photo } from "./api.js";

interface Entry {
  img: HTMLImageElement;
  decoded: boolean;
  ready: Promise<void>;
}

export class Preloader {
  private cache = new Map<number, Entry>();

  get(id: number): Entry | undefined {
    return this.cache.get(id);
  }

  ensure(p: Photo): Entry {
    let e = this.cache.get(p.id);
    if (!e) {
      const img = new Image();
      img.decoding = "async";
      img.src = imgUrl(p, "preview");
      const entry: Entry = { img, decoded: false, ready: Promise.resolve() };
      entry.ready = img.decode().then(
        () => {
          entry.decoded = true;
        },
        () => {},
      );
      this.cache.set(p.id, entry);
      e = entry;
    }
    return e;
  }

  /** 只保留这些照片（按顺序加载：第一张是当前图），其余取消下载并释放内存。 */
  keep(photos: Photo[]): void {
    const ids = new Set(photos.map((p) => p.id));
    for (const p of photos) this.ensure(p);
    for (const [id, e] of this.cache) {
      if (!ids.has(id)) {
        e.img.src = "";
        this.cache.delete(id);
      }
    }
  }

  clear(): void {
    this.keep([]);
  }
}

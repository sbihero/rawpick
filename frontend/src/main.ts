// 入口（管理员端）：按地址栏 # 路由切换首页、筛选界面、设置页和客户相册。

import { CullView } from "./cull.js";
import { GalleriesView, GalleryView } from "./galleries.js";
import { HomeView } from "./home.js";
import { loadKeymap } from "./keymap.js";
import { SettingsView } from "./settings.js";

const root = document.getElementById("app")!;
let view: { destroy(): void } | null = null;

function route(): void {
  view?.destroy();
  view = null;
  document.querySelectorAll(".backdrop").forEach((el) => el.remove());
  const hash = location.hash;
  let m: RegExpMatchArray | null;
  if ((m = hash.match(/^#\/s\/(\d+)/))) view = new CullView(root, Number(m[1]));
  else if ((m = hash.match(/^#\/g\/(\d+)/))) view = new GalleryView(root, Number(m[1]));
  else if (hash.startsWith("#/galleries")) view = new GalleriesView(root);
  else if (hash.startsWith("#/settings")) view = new SettingsView(root);
  else view = new HomeView(root);
}

window.addEventListener("hashchange", route);
// 先读取自定义快捷键，再显示页面
void loadKeymap().then(route);

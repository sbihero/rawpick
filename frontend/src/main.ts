// 入口：按地址栏 # 路由切换首页和筛选界面。

import { CullView } from "./cull.js";
import { renderHome } from "./home.js";

const root = document.getElementById("app")!;
let view: CullView | null = null;

function route(): void {
  view?.destroy();
  view = null;
  document.querySelectorAll(".backdrop").forEach((el) => el.remove());
  const m = location.hash.match(/^#\/s\/(\d+)/);
  if (m) {
    view = new CullView(root, Number(m[1]));
  } else {
    document.title = "rawpick";
    void renderHome(root);
  }
}

window.addEventListener("hashchange", route);
route();

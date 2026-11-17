// 入口：按地址栏 # 路由切换首页、筛选界面和设置页。
import { CullView } from "./cull.js";
import { HomeView } from "./home.js";
import { loadKeymap } from "./keymap.js";
import { SettingsView } from "./settings.js";
const root = document.getElementById("app");
let view = null;
function route() {
    view?.destroy();
    view = null;
    document.querySelectorAll(".backdrop").forEach((el) => el.remove());
    const m = location.hash.match(/^#\/s\/(\d+)/);
    if (m)
        view = new CullView(root, Number(m[1]));
    else if (location.hash.startsWith("#/settings"))
        view = new SettingsView(root);
    else
        view = new HomeView(root);
}
window.addEventListener("hashchange", route);
// 先读取自定义快捷键，再显示页面
void loadKeymap().then(route);

import { createApp } from "vue";
import App from "./App.vue";
import { router } from "./router";
import "./workspace.css";
import "./home.css";
import "./product.css";
const app = createApp(App).use(router);
void router.isReady().then(() => app.mount("#app"));

import { createApp, createRouter, createWebHashHistory, reactive } from "./vue.js";
import { registerPrimeVue } from "./components.js";
import { api, getToken, setToken } from "./api.js";
import {
  store,
  applyTheme,
  toggleTheme,
  toggleSidebar,
  loadMe,
  loadSummary,
  loadCapabilities,
  loadFeatures,
  refreshAlerts,
  logout,
  can,
  isRouteEnabled,
} from "./store.js";
import { t, i18n } from "./i18n.js";
import { ReconnectingWS } from "./ws.js";

import LoginPage from "./pages/login.js";
import OverviewPage from "./pages/overview.js";
import NetworkPage from "./pages/network.js";
import HardwarePage from "./pages/hardware.js";
import InterfacesPage from "./pages/interfaces.js";
import MediaPage from "./pages/media.js";
import StoragePage from "./pages/storage.js";
import ThermalPage from "./pages/thermal.js";
import SoftwarePage from "./pages/software.js";
import SysadminPage from "./pages/sysadmin.js";
import OtaPage from "./pages/ota.js";
import FilesPage from "./pages/files.js";
import SecurityPage from "./pages/security.js";
import CloudPage from "./pages/cloud.js";
import SettingsPage from "./pages/settings.js";
import NotificationsPage from "./pages/notifications.js";
import BackupPage from "./pages/backup.js";
import MaintenancePage from "./pages/maintenance.js";
import IntegrationsPage from "./pages/integrations.js";
import AccessPage from "./pages/access.js";
import SessionsPage from "./pages/sessions.js";
import RolesPage from "./pages/roles.js";
import GroupsPage from "./pages/groups.js";
import PolicyPage from "./pages/policy.js";
import DirectoryPage from "./pages/directory.js";
import TelemetryPage from "./pages/telemetry.js";
import SchedulesPage from "./pages/schedules.js";
import JournalPage from "./pages/journal.js";
import AboutPage from "./pages/about.js";

const NAV = [
  { to: "/home", icon: "pi pi-home", key: "nav.home" },
  { to: "/network", icon: "pi pi-globe", key: "nav.network" },
  { to: "/hardware", icon: "pi pi-microchip", key: "nav.hardware" },
  { to: "/interfaces", icon: "pi pi-sliders-h", key: "nav.interfaces" },
  { to: "/media", icon: "pi pi-camera", key: "nav.media" },
  { to: "/storage", icon: "pi pi-database", key: "nav.storage" },
  { to: "/thermal", icon: "pi pi-sun", key: "nav.thermal" },
  { to: "/software", icon: "pi pi-box", key: "nav.software" },
  { to: "/sysadmin", icon: "pi pi-server", key: "nav.sysadmin" },
  { to: "/ota", icon: "pi pi-download", key: "nav.ota" },
  { to: "/files", icon: "pi pi-folder", key: "nav.files" },
  { to: "/security", icon: "pi pi-shield", key: "nav.security" },
  { to: "/access", icon: "pi pi-lock", key: "nav.access" },
  { to: "/cloud", icon: "pi pi-cloud", key: "nav.cloud" },
  { to: "/telemetry", icon: "pi pi-chart-line", key: "nav.telemetry" },
  { to: "/notifications", icon: "pi pi-bell", key: "nav.notifications" },
  { to: "/backup", icon: "pi pi-history", key: "nav.backup" },
  { to: "/journal", icon: "pi pi-book", key: "nav.journal" },
  { to: "/schedules", icon: "pi pi-calendar", key: "nav.schedules" },
  { to: "/users", icon: "pi pi-users", key: "nav.users", children: [
    { to: "/sessions", icon: "pi pi-id-card", key: "nav.sessions" },
    { to: "/roles", icon: "pi pi-key", key: "nav.roles" },
    { to: "/groups", icon: "pi pi-users", key: "nav.groups" },
    { to: "/policy", icon: "pi pi-shield", key: "nav.policy" },
    { to: "/directory", icon: "pi pi-sitemap", key: "nav.directory" },
  ]},
  { to: "/settings", icon: "pi pi-cog", key: "nav.settings", children: [
    { to: "/integrations", icon: "pi pi-link", key: "nav.integrations" },
    { to: "/maintenance", icon: "pi pi-wrench", key: "nav.maintenance" },
    { to: "/about", icon: "pi pi-info-circle", key: "nav.about" },
  ]},
];

const routes = [
  { path: "/", redirect: "/home" },
  { path: "/login", component: LoginPage, meta: { public: true } },
  { path: "/home", component: OverviewPage },
  { path: "/network", component: NetworkPage },
  { path: "/hardware", component: HardwarePage },
  { path: "/interfaces", component: InterfacesPage },
  { path: "/media", component: MediaPage },
  { path: "/storage", component: StoragePage },
  { path: "/thermal", component: ThermalPage },
  { path: "/software", component: SoftwarePage },
  { path: "/sysadmin", component: SysadminPage },
  { path: "/ota", component: OtaPage },
  { path: "/files", component: FilesPage },
  { path: "/security", component: SecurityPage },
  { path: "/access", component: AccessPage },
  { path: "/cloud", component: CloudPage },
  { path: "/telemetry", component: TelemetryPage },
  { path: "/notifications", component: NotificationsPage },
  { path: "/backup", component: BackupPage },
  { path: "/journal", component: JournalPage },
  { path: "/schedules", component: SchedulesPage },
  { path: "/sessions", component: SessionsPage },
  { path: "/roles", component: RolesPage },
  { path: "/groups", component: GroupsPage },
  { path: "/policy", component: PolicyPage },
  { path: "/directory", component: DirectoryPage },
  { path: "/integrations", component: IntegrationsPage },
  { path: "/maintenance", component: MaintenancePage },
  { path: "/about", component: AboutPage },
  { path: "/settings", component: SettingsPage },
];

const router = createRouter({ history: createWebHashHistory(), routes });

router.beforeEach(async (to) => {
  if (to.meta.public) return true;
  if (!getToken()) return "/login";
  if (!store.user) {
    try {
      await loadMe();
      await loadSummary();
      await loadCapabilities();
      await loadFeatures();
      await refreshAlerts();
    } catch (e) {
      setToken(null);
      return "/login";
    }
  } else if (!store.features) {
    await loadFeatures();
  }
  // Capability-manifest gate (#116, audit Config). A direct link to a
  // disabled module's route (e.g. /ota when ota is disabled) redirects
  // to /home rather than rendering a half-broken page. The nav
  // already hides the entry, so this is the safety net for typed URLs,
  // bookmarks, and back-button navigation.
  if (!isRouteEnabled(to.path)) {
    return "/home";
  }
  return true;
});

const App = {
  setup() {
    const ui = reactive({ sidebarOpen: false });
    let metricsWs = null;
    let alertsWs = null;

    function startSockets() {
      if (metricsWs) return;
      metricsWs = new ReconnectingWS("/ws/metrics", {
        onState: (s) => (store.wsState = s),
        onMessage: () => { },
      });
      metricsWs.connect();
      alertsWs = new ReconnectingWS("/ws/alerts", {
        onMessage: () => refreshAlerts(),
        onState: () => { },
      });
      alertsWs.connect();
    }

    return {
      ui,
      store,
      t,
      i18n,
      NAV,
      can,
      isRouteEnabled,
      toggleTheme,
      toggleSidebar,
      logout,
      startSockets,
      router,
    };
  },
  methods: {
    roleSeverity(role) {
      if (role === "admin") return "danger";
      if (role === "operator") return "warning";
      return "info";
    },
  },
  mounted() {
    applyTheme();
    // Apply persisted sidebar state on mount; the .layout element exists by
    // the time this runs (we only get here once the SPA shell has rendered).
    const layout = this.$el && this.$el.querySelector
      ? this.$el.querySelector(".layout")
      : document.querySelector(".layout");
    if (layout) {
      layout.classList.toggle("sidebar-collapsed", store.sidebarCollapsed);
    }
    if (getToken()) this.startSockets();
  },
  watch: {
    "store.user"(v) {
      if (v) this.startSockets();
    },
  },
  template: `
    <router-view v-if="$route.meta.public"></router-view>
    <div v-else class="layout" :class="{ 'sidebar-collapsed': store.sidebarCollapsed }">
      <Toast />
      <ConfirmDialog />
      <aside class="sidebar" :class="{ open: ui.sidebarOpen }">
        <div class="brand">
          <span class="brand-mark"><i class="pi pi-server"></i></span>
          <span class="brand-text">{{ t('app.title') }}</span>
        </div>
        <router-link
          v-for="n in NAV.filter((n) => isRouteEnabled(n.to) || (n.children && n.children.some((c) => isRouteEnabled(c.to))))"
          :key="n.to || n.key"
          :to="n.to || (n.children && n.children.find((c) => isRouteEnabled(c.to)) || {}).to || '/home'"
          :data-label="t(n.key)"
          @click="ui.sidebarOpen=false"
        >
          <i :class="n.icon"></i><span class="nav-label">{{ t(n.key) }}</span>
        </router-link>
        <div v-for="n in NAV.filter((n) => n.children)" :key="'sub-' + n.key" class="nav-group">
          <div class="nav-group-label" v-if="!store.sidebarCollapsed">{{ t(n.key) }}</div>
          <router-link
            v-for="c in (n.children || []).filter((c) => isRouteEnabled(c.to))"
            :key="c.to"
            :to="c.to"
            class="nav-sub"
            :data-label="t(c.key)"
            @click="ui.sidebarOpen=false"
          >
            <i :class="c.icon"></i><span class="nav-label">{{ t(c.key) }}</span>
          </router-link>
        </div>
        <div class="sidebar-footer">
          <button
            class="sidebar-toggle"
            type="button"
            :aria-label="store.sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'"
            :title="store.sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'"
            @click="toggleSidebar"
          >
            <i :class="store.sidebarCollapsed ? 'pi pi-angle-double-right' : 'pi pi-angle-double-left'"></i>
          </button>
        </div>
      </aside>
      <div class="main">
        <div class="topbar">
          <div class="window-controls" aria-hidden="true">
            <span class="dot close"></span>
            <span class="dot minimize"></span>
            <span class="dot zoom"></span>
          </div>
          <Button class="hamburger" text icon="pi pi-bars" @click="ui.sidebarOpen=!ui.sidebarOpen" />
          <strong>{{ store.summary ? store.summary.device_name : '' }}</strong>
          <Tag
            v-if="store.summary && store.summary.any_simulated"
            severity="warning"
            :value="t('common.simulated')"
            icon="pi pi-desktop"
          />
          <div class="spacer"></div>
          <span class="bell" :title="store.alertsUnread + ' alerts'">
            <i class="pi pi-bell"></i>
            <Badge
              v-if="store.alertsUnread"
              :value="store.alertsUnread"
              severity="danger"
            />
          </span>
          <Button
            text
            :icon="store.theme==='dark' ? 'pi pi-sun' : 'pi pi-moon'"
            @click="toggleTheme"
            :title="store.theme==='dark' ? 'Switch to light theme' : 'Switch to dark theme'"
          />
          <Tag
            v-if="store.user"
            :value="store.user.role"
            :severity="roleSeverity(store.user.role)"
          />
          <Button
            text
            icon="pi pi-sign-out"
            :label="t('common.logout')"
            @click="logout"
          />
        </div>
        <div v-if="store.wsState==='reconnecting'" class="reconnect-banner">
          {{ t('common.reconnecting') }}
        </div>
        <div class="content">
          <router-view></router-view>
        </div>
      </div>
    </div>
  `,
};

const app = createApp(App);
registerPrimeVue(app);
app.use(router);

// Global error handler (Guardrail 1 / #119). Without it, an uncaught
// error inside a component's setup() or render() leaves the SPA in a
// half-mounted state with no UI for the user. The handler logs to
// console for the developer, and renders a fallback banner with a
// "Reload" button — the only sane recovery for an unknown failure.
app.config.errorHandler = (err, instance, info) => {
  // eslint-disable-next-line no-console
  console.error("[SPA uncaught error]", err, info);
  const el = document.getElementById("app");
  if (el && !document.getElementById("spa-error-boundary")) {
    const banner = document.createElement("div");
    banner.id = "spa-error-boundary";
    banner.setAttribute("role", "alert");
    banner.style.cssText =
      "padding:1rem 1.5rem;margin:1rem;border:1px solid #d33;border-radius:6px;background:#fee;color:#900;font-family:system-ui,sans-serif;";
    // Build via DOM APIs to satisfy the XSS guard (no innerHTML).
    const strong = document.createElement("strong");
    strong.textContent = "An unexpected error occurred. ";
    const span = document.createElement("span");
    span.textContent = "The dashboard state may be inconsistent. ";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.style.marginLeft = "1rem";
    btn.textContent = "Reload";
    btn.addEventListener("click", () => location.reload());
    banner.append(strong, span, btn);
    el.prepend(banner);
  }
};

app.mount("#app");

// Global reactive store: auth, device summary, alerts, theme, connection state.
import { reactive } from "./vue.js";
import { api, setToken } from "./api.js";

export const store = reactive({
    user: null, // {username, role, must_change_password}
    summary: null, // device summary
    capabilities: null, // active platform + per-module {enabled, apply_mode}
    features: null, // resolved feature tree (groups → subfeatures)
    alertsUnread: 0,
    theme: localStorage.getItem("theme") || "light",
    sidebarCollapsed:
        localStorage.getItem("sidebarCollapsed") === "1",
    wsState: "connecting",
});

// Map: SPA hash route -> manifest module name. Mirrors the Python
// ROUTE_TO_MODULE table in app/config.py — keep the two in sync so a
// disabled module hides both the nav entry AND any direct link to its
// route (audit "Capability manifest", #116).
export const ROUTE_TO_MODULE = {
    "/home": "system",
    "/network": "networking",
    "/hardware": "peripherals",
    "/interfaces": "peripherals",
    "/media": "peripherals",
    "/storage": "storage",
    "/thermal": "health",
    "/software": "system",
    "/sysadmin": "services",
    "/ota": "ota",
    "/files": "logs",
    "/security": "security",
    "/cloud": "system",
    "/settings": "users",
};

// Map: route -> {feature group, subfeature} for the new pages. Routes
// without a subfeature are gated on the group alone. The backend's
// `app/router_modules.py` is the source of truth on which routers
// actually mounted; the SPA uses this map to decide which nav entries
// to render.
export const ROUTE_TO_FEATURE = {
    "/home": ["overview", ""],
    "/network": ["network", "interfaces"],
    "/hardware": ["hardware", ""],
    "/interfaces": ["network", "interfaces"],
    "/media": ["hardware", "camera"],
    "/storage": ["storage", "disks"],
    "/thermal": ["thermal", "zones"],
    "/software": ["ota", "system_ota"],
    "/sysadmin": ["settings", "system"],
    "/ota": ["ota", "system_ota"],
    "/files": ["logs", "application"],
    "/security": ["security", "firewall"],
    "/cloud": ["cloud", "connection"],
    "/settings": ["settings", ""],
    "/notifications": ["settings", "notifications"],
    "/backup": ["settings", "backup_restore"],
    "/maintenance": ["settings", "maintenance"],
    "/integrations": ["settings", "integrations"],
    "/access": ["security", "access_control"],
    "/sessions": ["users", "sessions"],
    "/roles": ["users", "roles"],
    "/groups": ["users", "groups"],
    "/policy": ["users", "authentication"],
    "/directory": ["users", "directory"],
    "/telemetry": ["telemetry", ""],
    "/schedules": ["settings", "maintenance"],
    "/journal": ["logs", "journal"],
    "/about": ["settings", "about"],
};

export function isModuleEnabled(name) {
    if (!store.capabilities || !store.capabilities.modules) return true;
    const m = store.capabilities.modules[name];
    return !m || m.enabled !== false;
}

export function isRouteEnabled(path) {
    // New per-sub-feature gate takes precedence; falls back to the
    // coarse ROUTE_TO_MODULE check for routes that don't have a
    // feature mapping.
    const f = ROUTE_TO_FEATURE[path];
    if (f && store.features) {
        const [group, sub] = f;
        if (sub) {
            const grp = store.features.groups && store.features.groups[group];
            if (grp && grp.subfeatures && sub in grp.subfeatures) {
                return !!grp.subfeatures[sub];
            }
        } else {
            const grp = store.features.groups && store.features.groups[group];
            if (grp) return !!grp.enabled;
        }
    }
    const mod = ROUTE_TO_MODULE[path];
    if (!mod) return true;
    return isModuleEnabled(mod);
}

export function isSubFeatureEnabled(group, sub) {
    if (!store.features || !store.features.groups) return true;
    const grp = store.features.groups[group];
    if (!grp) return true;
    if (sub) {
        if (grp.subfeatures && sub in grp.subfeatures) return !!grp.subfeatures[sub];
    }
    return !!grp.enabled;
}

export function can(minRole) {
    const rank = { viewer: 0, operator: 1, admin: 2 };
    if (!store.user) return false;
    return rank[store.user.role] >= rank[minRole];
}

export function applyTheme() {
    const light = document.getElementById("theme-light");
    const dark = document.getElementById("theme-dark");
    const root = document.documentElement;
    if (store.theme === "dark") {
        light.disabled = true;
        dark.disabled = false;
        root.style.colorScheme = "dark";
        root.classList.add("app-dark");
        root.classList.remove("app-light");
    } else {
        light.disabled = false;
        dark.disabled = true;
        root.style.colorScheme = "light";
        root.classList.add("app-light");
        root.classList.remove("app-dark");
    }
}

export function toggleTheme() {
    store.theme = store.theme === "dark" ? "light" : "dark";
    localStorage.setItem("theme", store.theme);
    applyTheme();
}

export function toggleSidebar() {
    store.sidebarCollapsed = !store.sidebarCollapsed;
    localStorage.setItem(
        "sidebarCollapsed",
        store.sidebarCollapsed ? "1" : "0"
    );
    const layout = document.querySelector(".layout");
    if (layout) {
        layout.classList.toggle("sidebar-collapsed", store.sidebarCollapsed);
    }
}

export async function loadMe() {
    store.user = await api.get("/api/me");
    return store.user;
}

export async function loadSummary() {
    store.summary = await api.get("/api/system/summary");
}

export async function loadCapabilities() {
    store.capabilities = await api.get("/api/system/capabilities");
}

export async function loadFeatures() {
    try {
        store.features = await api.get("/api/config/features");
    } catch (_) {
        store.features = { groups: {}, schema_version: 1 };
    }
    return store.features;
}

export async function refreshAlerts() {
    try {
        const r = await api.get("/api/cloud/alerts");
        store.alertsUnread = r.unread || 0;
    } catch (_) { }
}

export function logout() {
    setToken(null);
    store.user = null;
    location.hash = "#/login";
}

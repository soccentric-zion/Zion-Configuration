import { api } from "../api.js";
import { can, store } from "../store.js";
import { i18n, setLocale, setUnits, t } from "../i18n.js";

export default {
    data() {
        return {
            sys: null, tz: [], restorePoints: null, backupPw: "",
            langs: ["en", "de", "zh"], units: ["metric", "imperial"], i18n, can,
            uiPrefs: { landing_page: null, items_per_page: null, refresh_interval_s: null },
            appearance: {
                theme: null, accent_color: null, date_format: null,
                time_format: null, units: null, density: null, sidebar_default: null,
            },
            networkDefaults: {
                bind_address: null, port: null,
                allowed_origins: "", behind_proxy: false, trusted_proxies: "",
                restart_required_for: [],
            },
            authMethod: { method: "local", available: ["local","pam","ldap","sso"] },
            // NTP + keyboard layout (Features.md System Settings)
            ntpEnabled: true,
            ntpServersText: "pool.ntp.org, time.google.com",
            keyboardLayout: "",
            // Time sync (Features.md §Settings System — time sync)
            timeStatus: null,
            timeSyncing: false,
            keyboardLayoutOptions: [
                "us", "uk", "de", "fr", "es", "it", "pt", "ru", "ja", "zh", "ko", "in",
            ],
            landingOptions: [
                { label: "Home / Overview", value: "/home" },
                { label: "Network", value: "/network" },
                { label: "Hardware", value: "/hardware" },
                { label: "Storage", value: "/storage" },
                { label: "Security", value: "/security" },
                { label: "Settings", value: "/settings" },
            ],
            itemsPerPageOptions: [10, 25, 50, 100],
            refreshOptions: [1, 2, 5, 10, 30, 60],
            themeOptions: [
                { label: "Auto (follow OS)", value: "auto" },
                { label: "Light", value: "light" },
                { label: "Dark", value: "dark" },
            ],
            dateFormatOptions: [
                { label: "ISO (2026-06-21)", value: "iso" },
                { label: "ISO with time", value: "iso-time" },
                { label: "US (06/21/2026)", value: "us" },
                { label: "EU (21/06/2026)", value: "eu" },
            ],
            timeFormatOptions: [
                { label: "24-hour", value: "24h" },
                { label: "12-hour", value: "12h" },
            ],
            densityOptions: [
                { label: "Comfortable", value: "comfortable" },
                { label: "Compact", value: "compact" },
                { label: "Spacious", value: "spacious" },
            ],
            sidebarDefaultOptions: [
                { label: "Expanded", value: "expanded" },
                { label: "Collapsed", value: "collapsed" },
            ],
        };
    },
    async mounted() {
        [this.sys, this.restorePoints, this.uiPrefs, this.appearance] = await Promise.all([
            api.get("/api/settings/system"),
            api.get("/api/settings/restore-points"),
            api.get("/api/settings/ui-prefs"),
            api.get("/api/settings/appearance"),
        ]);
        try {
            const nd = await api.get("/api/settings/network-defaults");
            this.networkDefaults = {
                bind_address: nd.bind_address,
                port: nd.port,
                allowed_origins: (nd.allowed_origins || []).join(", "),
                behind_proxy: !!nd.behind_proxy,
                trusted_proxies: (nd.trusted_proxies || []).join(", "),
                restart_required_for: nd.restart_required_for || [],
            };
        } catch (e) { /* network defaults not yet readable — leave defaults */ }
        try {
            this.authMethod = await api.get("/api/settings/auth-method");
        } catch (e) { /* leave default */ }
        const tz = await api.get("/api/settings/timezones");
        this.tz = tz.timezones;
        // Seed NTP / keyboard from system GET (KV-backed, with defaults).
        if (this.sys) {
            this.ntpEnabled = !!this.sys.ntp_enabled;
            if (Array.isArray(this.sys.ntp_servers)) this.ntpServersText = this.sys.ntp_servers.join(", ");
            this.keyboardLayout = this.sys.keyboard_layout || "";
        }
        // Pull time sync status (read-only, viewer-reachable).
        try {
            this.timeStatus = await api.get("/api/settings/time/status");
        } catch (e) { /* leave null — UI hides the section */ }
    },
    methods: {
        t, setLocale, setUnits,
        fmtBytes(b) { return (b / 1024).toFixed(1) + " KB"; },
        async saveUiPrefs() {
            // Only send the keys that have a current value to make a
            // minimal partial-PUT.
            const body = {};
            if (this.uiPrefs.landing_page) body.landing_page = this.uiPrefs.landing_page;
            if (this.uiPrefs.items_per_page != null)
                body.items_per_page = Number(this.uiPrefs.items_per_page);
            if (this.uiPrefs.refresh_interval_s != null)
                body.refresh_interval_s = Number(this.uiPrefs.refresh_interval_s);
            await api.put("/api/settings/ui-prefs", body);
            this.$toast?.add({ severity: "success", summary: "Dashboard config saved", life: 2000 });
        },
        async saveAppearance() {
            // Only send the keys the operator actually changed so a
            // partial PUT doesn't reset the others. Theme, density,
            // sidebar-default, etc. all map 1:1 between SPA and KV.
            const body = {};
            for (const k of ["theme", "accent_color", "date_format", "time_format",
                             "units", "density", "sidebar_default"]) {
                if (this.appearance[k] != null) body[k] = this.appearance[k];
            }
            await api.put("/api/settings/appearance", body);
            // Theme/density take effect immediately — push them onto the
            // i18n store so the SPA doesn't need a refresh. Other prefs
            // (date/time format, units) already flow through the i18n
            // module's getUnits()/getDateFmt() helpers on next render.
            if (body.theme) i18n.theme = body.theme;
            if (body.density) i18n.density = body.density;
            this.$toast?.add({ severity: "success", summary: "Appearance saved", life: 2000 });
        },
        async saveNtp() {
            // NTP settings: comma- or whitespace-separated host[:port] list
            // (the SPA shows the raw textbox and parses on submit so the
            // operator sees their input verbatim; the server re-validates
            // each entry against the same regex).
            const servers = this.ntpServersText
                .split(/[,\s]+/).map(s => s.trim()).filter(Boolean);
            await api.put("/api/settings/ntp", { enabled: !!this.ntpEnabled, servers });
            this.sys = await api.get("/api/settings/system");
            this.$toast?.add({ severity: "success", summary: "NTP saved", life: 2000 });
        },
        async saveKeyboardLayout() {
            if (!this.keyboardLayout) { this.$toast?.add({ severity: "warn", summary: "Pick a layout", life: 2000 }); return; }
            await api.put("/api/settings/keyboard-layout", { layout: this.keyboardLayout });
            this.sys = await api.get("/api/settings/system");
            this.$toast?.add({ severity: "success", summary: "Keyboard layout saved", life: 2000 });
        },
        async syncTime() {
            // Admin-only "Sync time now" — admin check is in the router
            // (403 surfaces as an error toast). The button is gated by
            // ``v-if="can('admin')"`` so a viewer doesn't see it at all.
            this.timeSyncing = true;
            try {
                const r = await api.post("/api/settings/time/sync");
                if (r.applied) {
                    this.$toast?.add({ severity: "success", summary: "Time synced", detail: r.source, life: 2000 });
                } else {
                    this.$toast?.add({ severity: "warn", summary: "Time sync failed", detail: r.error || r.source, life: 4000 });
                }
                this.timeStatus = await api.get("/api/settings/time/status");
            } catch (e) {
                this.$toast?.add({ severity: "error", summary: "Time sync error", life: 3000 });
            } finally {
                this.timeSyncing = false;
            }
        },
        confirmReboot() {
            this.$confirm.require({
                message: "Reboot the device now?", header: "Confirm reboot", icon: "pi pi-power-off",
                accept: () => this.$confirm.require({
                    message: "Are you really sure? The device will go offline.",
                    header: "Confirm again", icon: "pi pi-exclamation-triangle", acceptClass: "p-button-danger",
                    accept: async () => { await api.post("/api/settings/reboot"); this.$toast.add({ severity: "warn", summary: "Reboot requested", life: 3000 }); }
                })
            });
        },
        confirmShutdown() {
            this.$confirm.require({
                message: "Shut down the device?", header: "Confirm shutdown", icon: "pi pi-power-off",
                acceptClass: "p-button-danger",
                accept: async () => { await api.post("/api/settings/shutdown"); this.$toast.add({ severity: "warn", summary: "Shutdown requested", life: 3000 }); }
            });
        },
        async createBackup() {
            if (this.backupPw.length < 4) { this.$toast.add({ severity: "warn", summary: "Password too short", life: 2000 }); return; }
            const res = await fetch("/api/settings/backup", {
                method: "POST",
                headers: { "Content-Type": "application/json", Authorization: "Bearer " + (localStorage.getItem("token") || "") },
                body: JSON.stringify({ password: this.backupPw })
            });
            const blob = await res.blob();
            const url = URL.createObjectURL(blob); const a = document.createElement("a");
            a.href = url; a.download = "dashboard-backup.enc"; a.click(); URL.revokeObjectURL(url);
        },
        async addRestorePoint() {
            await api.post("/api/settings/restore-points", { name: "manual " + new Date().toISOString() });
            this.restorePoints = await api.get("/api/settings/restore-points");
        },
        async saveNetworkDefaults() {
            // Comma- or whitespace-separated lists — same parsing rule
            // as the NTP servers field, so the operator sees their
            // input verbatim. Server re-validates the same way.
            const origins = (this.networkDefaults.allowed_origins || "")
                .split(/[,\s]+/).map(s => s.trim()).filter(Boolean);
            const proxies = (this.networkDefaults.trusted_proxies || "")
                .split(/[,\s]+/).map(s => s.trim()).filter(Boolean);
            await api.put("/api/settings/network-defaults", {
                bind_address: this.networkDefaults.bind_address,
                allowed_origins: origins,
                behind_proxy: !!this.networkDefaults.behind_proxy,
                trusted_proxies: proxies,
            });
            this.$toast?.add({ severity: "success", summary: "Network defaults saved", life: 2000 });
        },
        async saveAuthMethod() {
            await api.put("/api/settings/auth-method", { method: this.authMethod.method });
            this.$toast?.add({ severity: "success", summary: "Auth method: " + this.authMethod.method, life: 2000 });
        },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.settings') }}</h2>
      <div class="grid-cards">
        <Card><template #title>System</template><template #content>
          <div v-if="sys">
            <div class="kv"><span class="k">Hostname</span><span class="v">{{ sys.hostname }}</span></div>
            <div class="kv"><span class="k">Timezone</span><span class="v">{{ sys.timezone }}</span></div>
            <div class="kv"><span class="k">DB size</span><span class="v">{{ fmtBytes(sys.db_size_bytes) }}</span></div>
            <Dropdown v-if="tz.length" :options="tz" :modelValue="sys.timezone" filter class="mt-2" style="width:100%" />
          </div>
          <Divider v-if="timeStatus" />
          <div v-if="timeStatus" class="kv">
            <span class="k">Time sync</span>
            <span class="v">
              <Tag :severity="timeStatus.synchronized ? 'success' : 'warning'" :value="timeStatus.source + (timeStatus.synchronized ? ' · synced' : ' · not synced')" />
            </span>
          </div>
          <Button v-if="can('admin')" label="Sync time now" icon="pi pi-refresh" class="mt-2"
                  :loading="timeSyncing" @click="syncTime" />
          <Divider v-if="can('admin')" />
          <div v-if="can('admin')" class="kv"><span class="k">NTP enabled</span>
            <InputSwitch v-model="ntpEnabled" /></div>
          <div v-if="can('admin')" class="kv"><span class="k">NTP servers</span>
            <InputText v-model="ntpServersText" placeholder="pool.ntp.org, time.google.com"
                       style="width:100%" /></div>
          <Button v-if="can('admin')" label="Save NTP" icon="pi pi-save" class="mt-2"
                  @click="saveNtp" />
          <Divider v-if="can('admin')" />
          <div v-if="can('admin')" class="kv"><span class="k">Keyboard layout</span>
            <Dropdown :options="keyboardLayoutOptions" v-model="keyboardLayout"
                      placeholder="select layout" style="min-width:10rem" /></div>
          <Button v-if="can('admin')" label="Save keyboard" icon="pi pi-save" class="mt-2"
                  @click="saveKeyboardLayout" />
        </template></Card>
        <Card><template #title>Localization</template><template #content>
          <div class="kv"><span class="k">Language</span>
            <Dropdown :options="langs" :modelValue="i18n.locale" @update:modelValue="setLocale" /></div>
          <div class="kv"><span class="k">Units</span>
            <SelectButton :options="units" :modelValue="i18n.units" @update:modelValue="setUnits" /></div>
        </template></Card>
        <Card><template #title>Dashboard Config</template><template #content>
          <div class="kv"><span class="k">Landing page</span>
            <Dropdown :options="landingOptions" optionLabel="label" optionValue="value"
                      v-model="uiPrefs.landing_page" style="min-width:14rem" /></div>
          <div class="kv"><span class="k">Items per page</span>
            <Dropdown :options="itemsPerPageOptions" v-model.number="uiPrefs.items_per_page"
                      style="min-width:8rem" /></div>
          <div class="kv"><span class="k">Live refresh (s)</span>
            <Dropdown :options="refreshOptions" v-model.number="uiPrefs.refresh_interval_s"
                      style="min-width:8rem" /></div>
          <Button label="Save" icon="pi pi-save" class="mt-2" @click="saveUiPrefs" />
        </template></Card>
        <Card v-if="can('admin')"><template #title>Appearance</template><template #content>
          <div class="kv"><span class="k">Theme</span>
            <SelectButton :options="themeOptions" optionLabel="label" optionValue="value"
                          v-model="appearance.theme" /></div>
          <div class="kv"><span class="k">Accent color</span>
            <InputText v-model="appearance.accent_color" placeholder="#3b82f6 or oklch(...)"
                       style="min-width:14rem" /></div>
          <div class="kv"><span class="k">Date format</span>
            <Dropdown :options="dateFormatOptions" optionLabel="label" optionValue="value"
                      v-model="appearance.date_format" style="min-width:12rem" /></div>
          <div class="kv"><span class="k">Time format</span>
            <SelectButton :options="timeFormatOptions" optionLabel="label" optionValue="value"
                          v-model="appearance.time_format" /></div>
          <div class="kv"><span class="k">Units</span>
            <SelectButton :options="['metric','imperial']" v-model="appearance.units" /></div>
          <div class="kv"><span class="k">Density</span>
            <Dropdown :options="densityOptions" optionLabel="label" optionValue="value"
                      v-model="appearance.density" style="min-width:12rem" /></div>
          <div class="kv"><span class="k">Sidebar default</span>
            <SelectButton :options="sidebarDefaultOptions" optionLabel="label" optionValue="value"
                          v-model="appearance.sidebar_default" /></div>
          <Button label="Save" icon="pi pi-save" class="mt-2" @click="saveAppearance" />
        </template></Card>
        <Card v-if="can('admin')"><template #title>Backup / Restore</template><template #content>
          <InputText v-model="backupPw" type="password" placeholder="backup password" style="width:100%" />
          <Button label="Create encrypted backup" icon="pi pi-download" class="mt-2" @click="createBackup" />
          <Divider />
          <Button label="Add restore point" icon="pi pi-plus" outlined size="small" @click="addRestorePoint" />
          <DataTable v-if="restorePoints" :value="restorePoints.restore_points" size="small" class="mt-2" scrollHeight="160px" scrollable>
            <Column field="ts" header="When"></Column><Column field="name" header="Name"></Column>
          </DataTable>
        </template></Card>
        <Card v-if="can('admin')"><template #title>Power</template><template #content>
          <Button label="Reboot" icon="pi pi-refresh" severity="warning" class="mr-2" @click="confirmReboot" />
          <Button label="Shutdown" icon="pi pi-power-off" severity="danger" @click="confirmShutdown" />
        </template></Card>
        <Card v-if="can('admin')"><template #title>Network Defaults</template><template #content>
          <div class="kv"><span class="k">Bind address</span>
            <InputText v-model="networkDefaults.bind_address" placeholder="0.0.0.0" style="width:14rem" /></div>
          <div class="kv"><span class="k">Port (active)</span>
            <span class="v muted">{{ networkDefaults.port }} — restart required to change</span></div>
          <div class="kv"><span class="k">Allowed origins (CORS)</span>
            <InputText v-model="networkDefaults.allowed_origins" placeholder="https://app.example.com, https://ops.example.com"
                       style="width:100%" /></div>
          <div class="kv"><span class="k">Behind reverse proxy</span>
            <InputSwitch v-model="networkDefaults.behind_proxy" /></div>
          <div class="kv"><span class="k">Trusted proxies</span>
            <InputText v-model="networkDefaults.trusted_proxies" placeholder="10.0.0.0/8, 192.168.0.0/16"
                       style="width:100%" /></div>
          <Button label="Save" icon="pi pi-save" class="mt-2" @click="saveNetworkDefaults" />
        </template></Card>
        <Card v-if="can('admin')"><template #title>Auth Method</template><template #content>
          <div class="kv"><span class="k">Active</span>
            <SelectButton :options="authMethod.available" v-model="authMethod.method" /></div>
          <div class="kv mt-2"><span class="v muted">
            local is wired. pam / ldap / sso are stored as operator intent and will take effect when those backends are implemented.</span></div>
          <Button label="Save" icon="pi pi-save" class="mt-2" @click="saveAuthMethod" />
        </template></Card>
      </div>
    </div>
  `,
};

// Journal / dmesg / syslog viewer + app log manager.
import { api } from "../api.js";
import { can } from "../store.js";
import { t } from "../i18n.js";

export default {
    data() {
        return {
            can, journal: [], dmesg: [], syslog: [], units: [], apps: [],
            tailForm: { unit: "dashboard", lines: 200 },
            serviceLog: null, clearForm: { retention_days: 14 },
        };
    },
    async mounted() { await this.refresh(); },
    methods: {
        t,
        async refresh() {
            const [j, d, s, u, a] = await Promise.all([
                api.get("/api/logs/journal"), api.get("/api/logs/dmesg"),
                api.get("/api/logs/syslog"), api.get("/api/logs/units"),
                api.get("/api/files-log/apps"),
            ]);
            this.journal = (j && j.lines) || [];
            this.dmesg = (d && d.lines) || [];
            this.syslog = (s && s.lines) || [];
            this.units = (u && u.units) || [];
            this.apps = (a && a.apps) || [];
        },
        async tail() {
            this.serviceLog = await api.post("/api/logs/tail", { path: "/var/log/" + this.tailForm.unit + ".log", lines: this.tailForm.lines });
        },
        async loadService(unit) {
            this.serviceLog = await api.get("/api/logs/service/" + encodeURIComponent(unit));
        },
        async tailApp(p) {
            this.serviceLog = await api.post("/api/files-log/tail", { path: p, lines: 200 });
        },
        async clear() {
            this.$confirm.require({
                message: "Clear logs older than " + this.clearForm.retention_days + " days?",
                header: "Confirm", icon: "pi pi-trash",
                accept: async () => {
                    await api.post("/api/files-log/clear", { confirm: true, retention_days: this.clearForm.retention_days });
                    this.$toast.add({ severity: "success", summary: "Cleared" });
                    await this.refresh();
                },
            });
        },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.journal') }}</h2>
      <TabView>
        <TabPanel header="Journal">
          <pre v-if="journal.length" class="log-viewer">{{ journal.join('\\n') }}</pre>
          <p v-else class="text-muted">No journal entries</p>
        </TabPanel>
        <TabPanel header="dmesg">
          <pre v-if="dmesg.length" class="log-viewer">{{ dmesg.join('\\n') }}</pre>
          <p v-else class="text-muted">No dmesg entries</p>
        </TabPanel>
        <TabPanel header="Syslog">
          <pre v-if="syslog.length" class="log-viewer">{{ syslog.join('\\n') }}</pre>
          <p v-else class="text-muted">No syslog entries</p>
        </TabPanel>
        <TabPanel header="Service">
          <div class="flex gap-2 align-items-end mb-2">
            <span><label>Unit</label>
              <Dropdown v-model="tailForm.unit" :options="units" />
            </span>
            <span><label>Lines</label><InputNumber v-model="tailForm.lines" :min="10" :max="5000" /></span>
            <Button icon="pi pi-search" label="Tail" @click="tail" />
            <Button icon="pi pi-refresh" label="Load" outlined @click="loadService(tailForm.unit)" />
          </div>
          <pre v-if="serviceLog" class="log-viewer">{{ (serviceLog.lines || []).join('\\n') }}</pre>
        </TabPanel>
        <TabPanel header="Apps">
          <DataTable v-if="apps.length" :value="apps" size="small">
            <Column field="path" header="Path"></Column>
            <Column field="count" header="Files"></Column>
            <Column header=""><template #body="s" v-if="can('admin')">
              <Button text size="small" icon="pi pi-search" @click="tailApp(s.data.path)" />
            </template></Column>
          </DataTable>
          <Divider v-if="can('admin')" />
          <div v-if="can('admin')" class="flex gap-2 align-items-end">
            <span><label>Retention (days)</label><InputNumber v-model="clearForm.retention_days" :min="1" :max="365" /></span>
            <Button icon="pi pi-trash" label="Clear old logs" severity="warning" @click="clear" />
          </div>
        </TabPanel>
      </TabView>
    </div>
  `,
};

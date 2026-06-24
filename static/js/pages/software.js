import { api, getToken } from "../api.js";
import { ReconnectingWS } from "../ws.js";
import { can } from "../store.js";
import { t } from "../i18n.js";

export default {
    data() {
        return {
            services: null, containers: null, versions: null, history: null,
            otaPercent: 0, otaStatus: "", otaLine: "", otaWs: null, uploading: false,
            term: { lines: "", ws: null, open: false }, can,
        };
    },
    async mounted() { await this.refresh(); },
    beforeUnmount() {
        if (this.otaWs) this.otaWs.close();
        if (this.term.ws) this.term.ws.close();
    },
    methods: {
        t,
        async refresh() {
            [this.services, this.containers, this.versions, this.history] = await Promise.all([
                api.get("/api/software/services"), api.get("/api/software/containers"),
                api.get("/api/software/ota/versions"), api.get("/api/software/ota/history"),
            ]);
        },
        async svc(name, action) {
            await api.post(`/api/software/services/${name}/${action}`);
            this.$toast.add({ severity: "success", summary: `${name}: ${action}`, life: 1500 });
            await this.refresh();
        },
        async ctr(name, action) {
            await api.post(`/api/software/containers/${name}/${action}`);
            this.$toast.add({ severity: "success", summary: `${name}: ${action}`, life: 1500 });
            await this.refresh();
        },
        onFile(ev) {
            const file = ev.target.files[0];
            if (file) this.uploadSwu(file);
        },
        async uploadSwu(file) {
            this.uploading = true; this.otaPercent = 0; this.otaStatus = "uploading"; this.otaLine = "";
            if (!this.otaWs) {
                this.otaWs = new ReconnectingWS("/ws/ota", {
                    onMessage: (m) => {
                        if (m && typeof m.percent === "number") {
                            this.otaPercent = m.percent; this.otaStatus = m.status; this.otaLine = m.line;
                            if (m.percent >= 100) { this.uploading = false; this.refresh(); }
                        }
                    }
                });
                this.otaWs.connect();
            }
            const fd = new FormData(); fd.append("file", file, file.name);
            try {
                await api.upload("/api/software/ota/upload", fd);
            } catch (e) {
                this.otaStatus = "failed"; this.otaLine = e.message; this.uploading = false;
            }
        },
        openTerminal() {
            this.term.open = true; this.term.lines = "";
            this.term.ws = new ReconnectingWS("/ws/terminal", {
                onMessage: (m) => {
                    this.term.lines += typeof m === "string" ? m : "";
                    this.$nextTick(() => { const el = this.$refs.term; if (el) el.scrollTop = el.scrollHeight; });
                },
            });
            // terminal sends/receives raw bytes; override message handler to text
            this.term.ws.connect();
            const orig = this.term.ws.ws;
        },
        termKey(ev) {
            if (!this.term.ws) return;
            let data = ev.key;
            if (ev.key === "Enter") data = "\r";
            else if (ev.key === "Backspace") data = "\x7f";
            else if (ev.key.length > 1) return;
            this.term.ws.sendRaw(data);
            ev.preventDefault();
        },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.software') }}</h2>
      <TabView>
        <TabPanel header="Services">
          <DataTable v-if="services" :value="services.units" size="small" scrollable scrollHeight="360px">
            <Column field="name" header="Unit"></Column>
            <Column field="active" header="Active"></Column>
            <Column field="enabled" header="Enabled"></Column>
            <Column header="Actions"><template #body="s">
              <span v-if="can('operator')">
                <Button size="small" text icon="pi pi-play" @click="svc(s.data.name,'start')" />
                <Button size="small" text icon="pi pi-stop" @click="svc(s.data.name,'stop')" />
                <Button size="small" text icon="pi pi-refresh" @click="svc(s.data.name,'restart')" />
              </span></template></Column>
          </DataTable>
        </TabPanel>
        <TabPanel header="Containers">
          <DataTable v-if="containers" :value="containers.containers" size="small">
            <Column field="name" header="Name"></Column>
            <Column field="image" header="Image"></Column>
            <Column field="state" header="State"></Column>
            <Column header="CPU"><template #body="s">{{ s.data.cpu_pct }}%</template></Column>
            <Column header="Actions"><template #body="s">
              <span v-if="can('admin')">
                <Button size="small" text icon="pi pi-play" @click="ctr(s.data.name,'start')" />
                <Button size="small" text icon="pi pi-stop" @click="ctr(s.data.name,'stop')" />
                <Button size="small" text icon="pi pi-trash" @click="ctr(s.data.name,'remove')" />
              </span></template></Column>
          </DataTable>
        </TabPanel>
        <TabPanel header="OTA Update">
          <Card><template #title>Installed versions</template><template #content>
            <DataTable v-if="versions" :value="versions.versions" size="small">
              <Column field="component" header="Component"></Column><Column field="version" header="Version"></Column>
            </DataTable>
            <Divider />
            <div v-if="can('admin')">
              <input type="file" accept=".swu" data-testid="ota-file" @change="onFile" :disabled="uploading" />
              <ProgressBar :value="otaPercent" class="mt-2" data-testid="ota-progress" />
              <small>{{ otaStatus }} — {{ otaLine }}</small>
            </div>
            <Divider />
            <DataTable v-if="history" :value="history.history" size="small" scrollable scrollHeight="160px">
              <Column field="ts" header="When"></Column><Column field="version" header="Version"></Column>
              <Column field="result" header="Result"></Column>
            </DataTable>
          </template></Card>
        </TabPanel>
        <TabPanel header="Web Terminal" v-if="can('admin')">
          <Button label="Open terminal" icon="pi pi-desktop" @click="openTerminal" data-testid="open-terminal" />
          <div v-if="term.open" ref="term" class="term-host mt-2" tabindex="0" @keydown="termKey">{{ term.lines }}</div>
          <small v-if="term.open">Click the panel and type. Connected to a PTY over WebSocket.</small>
        </TabPanel>
      </TabView>
    </div>
  `,
};

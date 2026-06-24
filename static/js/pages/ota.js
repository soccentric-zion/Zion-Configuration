import { api } from "../api.js";
import { can } from "../store.js";
import { t } from "../i18n.js";
import { ReconnectingWS } from "../ws.js";

export default {
    data() {
        return {
            can, status: null, history: [], system: null,
            uploading: false, otaPercent: 0, otaStatus: "", otaLine: "", otaWs: null,
            configText: '{\n  "name": "field-config-v2",\n  "config": {\n    "sample_rate_hz": 100\n  }\n}',
            timer: null,
        };
    },
    async mounted() { await this.refresh(); this.timer = setInterval(() => this.refresh(true), 5000); },
    beforeUnmount() { clearInterval(this.timer); if (this.otaWs) this.otaWs.close(); },
    methods: {
        t,
        async refresh() {
            const [status, history] = await Promise.all([
                api.get("/api/ota/status"), api.get("/api/ota/history"),
            ]);
            this.status = status; this.system = status.system; this.history = history.history;
        },
        onFile(ev) { const f = ev.target.files[0]; if (f) this.uploadApp(f); ev.target.value = ""; },
        async uploadApp(file) {
            this.uploading = true; this.otaPercent = 0; this.otaStatus = "uploading"; this.otaLine = "";
            if (!this.otaWs) {
                this.otaWs = new ReconnectingWS("/ws/ota", {
                    onMessage: (m) => {
                        if (m && typeof m.percent === "number") {
                            this.otaPercent = m.percent; this.otaStatus = m.status; this.otaLine = m.line;
                            if (m.percent >= 100) { this.uploading = false; this.refresh(); }
                        }
                    },
                });
                this.otaWs.connect();
            }
            const fd = new FormData(); fd.append("file", file, file.name);
            try { await api.upload("/api/ota/app", fd); }
            catch (e) { this.otaStatus = "failed"; this.otaLine = e.message; this.uploading = false; }
        },
        async pushConfig() {
            let body;
            try { body = JSON.parse(this.configText); }
            catch (e) { this.$toast.add({ severity: "error", summary: "Invalid JSON", detail: e.message, life: 3000 }); return; }
            const r = await api.post("/api/ota/config", body);
            this.$toast.add({ severity: "success", summary: "Config OTA applied", detail: "restore point #" + r.restore_point_id, life: 2500 });
            await this.refresh();
        },
        abSwitch(slot) {
            this.$confirm.require({
                message: `Switch boot to slot ${slot}? A reboot is required to take effect.`, header: "Confirm A/B switch",
                icon: "pi pi-exclamation-triangle",
                accept: async () => { await api.post("/api/ota/system/switch", { slot, confirm: true }); await this.refresh(); },
            });
        },
        abRollback() {
            this.$confirm.require({
                message: "Roll back to the fallback slot?", header: "Confirm rollback", icon: "pi pi-exclamation-triangle",
                accept: async () => { await api.post("/api/ota/system/rollback", { confirm: true }); await this.refresh(); },
            });
        },
        reboot() {
            this.$confirm.require({
                message: "Reboot the device now?", header: "Confirm reboot", icon: "pi pi-exclamation-triangle",
                accept: async () => { await api.post("/api/ota/reboot", { confirm: true, now: true }); },
            });
        },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.ota') }}
        <Tag v-if="status && status.simulated" severity="warning" :value="t('common.simulated')" icon="pi pi-desktop" class="ml-2" />
      </h2>
      <TabView>
        <TabPanel header="Application">
          <Card><template #title>Installed versions</template>
            <template #content>
              <DataTable :value="status ? status.versions : []" size="small">
                <Column field="component" header="Component"></Column>
                <Column field="version" header="Version"></Column>
              </DataTable>
              <div class="mt-3" v-if="can('admin')">
                <label class="block mb-1">Upload app/dashboard image (.swu)</label>
                <input type="file" accept=".swu" data-testid="ota-app-file" @change="onFile" :disabled="uploading" />
                <ProgressBar v-if="uploading || otaPercent>0" :value="otaPercent" class="mt-2" />
                <div v-if="otaStatus" class="text-muted mt-1">{{ otaStatus }} — {{ otaLine }}</div>
              </div>
            </template>
          </Card>
        </TabPanel>

        <TabPanel header="Configuration">
          <Card><template #title>Config OTA (no code)</template>
            <template #content>
              <p class="text-muted">Push device/config/system-config definitions. A restore point is created automatically.</p>
              <textarea v-model="configText" rows="10" style="width:100%;font-family:monospace;padding:.75rem;border-radius:8px;border:1px solid var(--surface-border)" :disabled="!can('admin')"></textarea>
              <Button v-if="can('admin')" class="mt-2" label="Apply config OTA" icon="pi pi-upload" data-testid="ota-config-apply" @click="pushConfig" />
            </template>
          </Card>
        </TabPanel>

        <TabPanel header="System (A/B)">
          <Card v-if="system"><template #title><i class="pi pi-sync"></i> A/B slots (golden boot)</template>
            <template #content>
              <div class="kv"><span class="k">active slot</span><span class="v"><Tag severity="success" :value="system.active_slot" /></span></div>
              <div class="kv"><span class="k">fallback slot</span><span class="v"><Tag :value="system.fallback_slot" /></span></div>
              <div class="kv"><span class="k">slot A attempts left</span><span class="v">{{ system.slot_a_left }}</span></div>
              <div class="kv"><span class="k">slot B attempts left</span><span class="v">{{ system.slot_b_left }}</span></div>
              <div class="kv"><span class="k">boot count</span><span class="v">{{ system.boot_count }}</span></div>
              <div class="kv"><span class="k">upgrade available</span><span class="v">{{ system.upgrade_available }}</span></div>
              <div class="mt-3" v-if="can('admin')">
                <Button size="small" class="mr-2" label="Switch to A" :disabled="system.active_slot==='A'" data-testid="ota-switch-a" @click="abSwitch('A')" />
                <Button size="small" class="mr-2" label="Switch to B" :disabled="system.active_slot==='B'" @click="abSwitch('B')" />
                <Button size="small" class="mr-2" severity="warning" label="Rollback" icon="pi pi-undo" @click="abRollback" />
                <Button size="small" severity="danger" label="Reboot" icon="pi pi-power-off" @click="reboot" />
              </div>
            </template>
          </Card>
        </TabPanel>

        <TabPanel header="History">
          <DataTable :value="history" size="small">
            <Column field="version" header="Update"></Column>
            <Column field="result" header="Result"><template #body="s"><Tag :severity="s.data.result==='success'?'success':'danger'" :value="s.data.result" /></template></Column>
            <Column field="duration_s" header="Duration (s)"></Column>
            <Column field="triggered_by" header="By"></Column>
            <Column field="ts" header="When"></Column>
          </DataTable>
        </TabPanel>
      </TabView>
    </div>
  `,
};

// Backup jobs, restore points, factory reset, config export/import.
import { api } from "../api.js";
import { can } from "../store.js";
import { t } from "../i18n.js";

export default {
    data() {
        return {
            can, jobs: null, restorePoints: null,
            newJob: { name: "", kind: "config", target: "", schedule: "@daily", enabled: true },
            kinds: ["config", "full", "factory"],
            fac: { confirm: false, ack_data_loss: false, ack_unrecoverable: false },
            facArmed: null,
        };
    },
    async mounted() { await this.refresh(); },
    methods: {
        t,
        async refresh() {
            [this.jobs, this.restorePoints] = await Promise.all([
                api.get("/api/backup/jobs"), api.get("/api/backup/restore-points"),
            ]);
        },
        async add() {
            await api.post("/api/backup/jobs", this.newJob);
            this.$toast.add({ severity: "success", summary: "Job added", life: 1500 });
            this.newJob = { name: "", kind: "config", target: "", schedule: "@daily", enabled: true };
            await this.refresh();
        },
        async run(j) { await api.post("/api/backup/jobs/" + j.id + "/run"); this.$toast.add({ severity: "success", summary: "Triggered " + j.name }); await this.refresh(); },
        async del(j) {
            this.$confirm.require({
                message: "Delete job '" + j.name + "'?", header: "Confirm", icon: "pi pi-trash",
                accept: async () => { await api.del("/api/backup/jobs/" + j.id); await this.refresh(); }
            });
        },
        async factoryArm() {
            if (!this.fac.confirm || !this.fac.ack_data_loss || !this.fac.ack_unrecoverable) {
                this.$toast.add({ severity: "warn", summary: "All three acknowledgements required" });
                return;
            }
            this.facArmed = await api.post("/api/backup/factory-reset", this.fac);
            this.$toast.add({ severity: "warn", summary: "Armed — confirm in " + this.facArmed.armed_until, life: 5000 });
        },
        async factoryCommit() {
            if (!this.facArmed) return;
            await api.post("/api/backup/factory-reset/confirm", { plan_id: this.facArmed.plan_id, confirm: true });
            this.facArmed = null;
            this.fac = { confirm: false, ack_data_loss: false, ack_unrecoverable: false };
            this.$toast.add({ severity: "success", summary: "Factory reset committed" });
        },
        async factoryRollback() {
            if (!this.facArmed) return;
            await api.post("/api/backup/factory-reset/rollback", { plan_id: this.facArmed.plan_id, confirm: true });
            this.facArmed = null;
            this.$toast.add({ severity: "info", summary: "Rollback" });
        },
        async exportConfig() {
            const r = await api.get("/api/backup/export");
            const blob = new Blob([JSON.stringify(r, null, 2)], { type: "application/json" });
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob); a.download = "dashboard-config.json"; a.click();
        },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.backup') }}</h2>
      <Card class="mb-3"><template #title>Backup jobs</template>
        <template #content>
          <DataTable v-if="jobs" :value="jobs.db" size="small">
            <Column field="name" header="Name"></Column>
            <Column field="kind" header="Kind"></Column>
            <Column field="target" header="Target"></Column>
            <Column field="schedule" header="Schedule"></Column>
            <Column header="Enabled"><template #body="s">
              <Tag :severity="s.data.enabled?'success':'secondary'" :value="String(!!s.data.enabled)" /></template></Column>
            <Column header=""><template #body="s" v-if="can('admin')">
              <Button text size="small" icon="pi pi-play" @click="run(s.data)" />
              <Button text size="small" icon="pi pi-trash" severity="danger" @click="del(s.data)" />
            </template></Column>
          </DataTable>
        </template>
      </Card>

      <div class="grid-cards">
        <Card v-if="can('admin')"><template #title>Add job</template>
          <template #content>
            <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:.5rem;align-items:end">
              <span><label>Name</label><InputText v-model="newJob.name" /></span>
              <span><label>Kind</label><Dropdown v-model="newJob.kind" :options="kinds" /></span>
              <span style="grid-column:span 2"><label>Target</label><InputText v-model="newJob.target" placeholder="/var/backups or nfs://..." /></span>
              <span><label>Schedule</label><InputText v-model="newJob.schedule" placeholder="@daily" /></span>
              <span><label>Enabled</label><InputSwitch v-model="newJob.enabled" /></span>
            </div>
            <Button class="mt-2" icon="pi pi-plus" label="Add" @click="add" />
          </template>
        </Card>
        <Card><template #title>Restore points</template>
          <template #content>
            <DataTable v-if="restorePoints" :value="restorePoints.points" size="small">
              <Column field="label" header="Label"></Column>
              <Column field="created_at" header="Created"></Column>
              <Column header="Size"><template #body="s">{{ Math.round(s.data.size_bytes/1024) }} KB</template></Column>
            </DataTable>
          </template>
        </Card>
      </div>

      <Card class="mt-3" v-if="can('admin')"><template #title><i class="pi pi-exclamation-triangle"></i> Factory reset</template>
        <template #content>
          <p class="text-muted">Wipes user data and reboots. Three acknowledgements are required. Once armed, the action auto-rolls back if the device never re-heartbeats.</p>
          <div style="display:flex;gap:1.5rem;flex-wrap:wrap;margin:1rem 0">
            <label><InputSwitch v-model="fac.confirm" /> I confirm</label>
            <label><InputSwitch v-model="fac.ack_data_loss" /> Data loss acknowledged</label>
            <label><InputSwitch v-model="fac.ack_unrecoverable" /> Not recoverable</label>
          </div>
          <Button v-if="!facArmed" icon="pi pi-bolt" label="Arm factory reset" severity="danger" @click="factoryArm" />
          <div v-else class="flex gap-2 align-items-center">
            <Tag severity="warning" :value="'Armed ' + facArmed.armed_until" />
            <Button icon="pi pi-check" label="Confirm" severity="danger" @click="factoryCommit" />
            <Button icon="pi pi-undo" label="Rollback" outlined @click="factoryRollback" />
          </div>
        </template>
      </Card>

      <Card class="mt-3" v-if="can('admin')"><template #title>Config export / import</template>
        <template #content>
          <Button icon="pi pi-download" label="Export config" @click="exportConfig" />
        </template>
      </Card>
    </div>
  `,
};

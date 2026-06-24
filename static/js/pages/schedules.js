// Scheduled jobs (cron-style maintenance, retention, etc.).
import { api } from "../api.js";
import { can } from "../store.js";
import { t } from "../i18n.js";

export default {
    data() {
        return {
            can, list: null,
            newJob: { name: "", cron: "0 3 * * *", action: "rotate_logs" },
            actions: ["rotate_logs", "vacuum_db", "cleanup_uploads", "reboot", "backup_run"],
        };
    },
    async mounted() { await this.refresh(); },
    methods: {
        t,
        async refresh() { this.list = await api.get("/api/jobs"); },
        async add() {
            await api.post("/api/jobs", this.newJob);
            this.$toast.add({ severity: "success", summary: "Job added", life: 1500 });
            this.newJob = { name: "", cron: "0 3 * * *", action: "rotate_logs" };
            await this.refresh();
        },
        async run(j) {
            await api.post("/api/jobs/" + j.id + "/run");
            this.$toast.add({ severity: "success", summary: "Triggered " + j.name, life: 1500 });
            await this.refresh();
        },
        async del(j) {
            this.$confirm.require({
                message: "Delete job '" + j.name + "'?", header: "Confirm", icon: "pi pi-trash",
                accept: async () => { await api.del("/api/jobs/" + j.id); await this.refresh(); }
            });
        },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.schedules') }}</h2>
      <Card class="mb-3" v-if="can('admin')"><template #title>Add scheduled job</template>
        <template #content>
          <div style="display:grid;grid-template-columns:2fr 1fr 1fr auto;gap:.5rem;align-items:end">
            <span><label>Name</label><InputText v-model="newJob.name" placeholder="rotate-logs" /></span>
            <span><label>Cron</label><InputText v-model="newJob.cron" placeholder="0 3 * * *" /></span>
            <span><label>Action</label><Dropdown v-model="newJob.action" :options="actions" /></span>
            <Button label="Add" icon="pi pi-plus" :disabled="!newJob.name" @click="add" />
          </div>
        </template>
      </Card>
      <Card><template #title>Jobs</template>
        <template #content>
          <DataTable v-if="list" :value="list.db" size="small">
            <Column field="name" header="Name"></Column>
            <Column field="cron" header="Cron"></Column>
            <Column field="action" header="Action"></Column>
            <Column header="Enabled"><template #body="s">
              <Tag :severity="s.data.enabled?'success':'secondary'" :value="String(!!s.data.enabled)" /></template></Column>
            <Column field="last_run_at" header="Last run"></Column>
            <Column field="last_status" header="Status"></Column>
            <Column header=""><template #body="s" v-if="can('admin')">
              <Button text size="small" icon="pi pi-play" @click="run(s.data)" />
              <Button text size="small" icon="pi pi-trash" severity="danger" @click="del(s.data)" />
            </template></Column>
          </DataTable>
        </template>
      </Card>
    </div>
  `,
};

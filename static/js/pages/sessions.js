// Active sessions list, history, max-concurrent.
import { api } from "../api.js";
import { can } from "../store.js";
import { t } from "../i18n.js";

export default {
    data() {
        return { can, active: null, history: null, maxForm: { limit: 4 } };
    },
    async mounted() { await this.refresh(); },
    methods: {
        t,
        async refresh() {
            [this.active, this.history] = await Promise.all([
                api.get("/api/sessions"), api.get("/api/sessions/history"),
            ]);
        },
        async kill(s) {
            this.$confirm.require({
                message: "Kill session for " + (s.username || s.id) + "?", header: "Confirm", icon: "pi pi-power-off",
                accept: async () => { await api.del("/api/sessions/" + s.id); await this.refresh(); }
            });
        },
        async setMax() {
            await api.post("/api/sessions/max-concurrent", this.maxForm);
            this.$toast.add({ severity: "success", summary: "Max sessions: " + this.maxForm.limit });
        },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.sessions') }}</h2>
      <Card class="mb-3"><template #title>Active</template>
        <template #content>
          <DataTable v-if="active" :value="active.sessions" size="small">
            <Column field="username" header="User"></Column>
            <Column field="ip" header="IP"></Column>
            <Column field="created_at" header="Started"></Column>
            <Column field="last_seen" header="Last seen"></Column>
            <Column header=""><template #body="s" v-if="can('admin')">
              <Button text size="small" icon="pi pi-power-off" severity="danger" @click="kill(s.data)" />
            </template></Column>
          </DataTable>
        </template>
      </Card>
      <div class="grid-cards">
        <Card><template #title>History</template>
          <template #content>
            <DataTable v-if="history" :value="history.sessions" size="small" :paginator="true" :rows="20">
              <Column field="username" header="User"></Column>
              <Column field="ip" header="IP"></Column>
              <Column field="started_at" header="Started"></Column>
              <Column field="ended_at" header="Ended"></Column>
              <Column field="reason" header="Reason"></Column>
            </DataTable>
          </template>
        </Card>
        <Card v-if="can('admin')"><template #title>Max concurrent</template>
          <template #content>
            <InputNumber v-model="maxForm.limit" :min="1" :max="64" class="mb-2" />
            <Button icon="pi pi-save" label="Apply" @click="setMax" />
          </template>
        </Card>
      </div>
    </div>
  `,
};

import { api } from "../api.js";
import { can, refreshAlerts } from "../store.js";
import { t } from "../i18n.js";

export default {
    data() {
        return {
            prov: null, telemetry: null, rules: null, alerts: null,
            newRule: { name: "", metric: "cpu_temp_c", condition: ">", threshold: 80, duration_s: 60, severity: "warning" },
            metrics: ["cpu_total", "mem_percent", "cpu_temp_c", "cpu_freq_mhz"],
            conditions: [">", "<", "==", "rate"], can
        };
    },
    async mounted() { await this.refresh(); },
    methods: {
        t,
        async refresh() {
            [this.prov, this.telemetry, this.rules, this.alerts] = await Promise.all([
                api.get("/api/cloud/provisioning"), api.get("/api/cloud/telemetry"),
                api.get("/api/cloud/alerts/rules"), api.get("/api/cloud/alerts"),
            ]);
        },
        async advance() { await api.post("/api/cloud/provisioning/advance"); await this.refresh(); },
        async claim() {
            const r = await api.post("/api/cloud/provisioning/claim-token");
            this.$toast.add({ severity: "info", summary: "Claim token", detail: r.claim_token, life: 6000 });
            await this.refresh();
        },
        async createRule() {
            await api.post("/api/cloud/alerts/rules", this.newRule);
            this.$toast.add({ severity: "success", summary: "Rule created", life: 1500 });
            await this.refresh();
        },
        async deleteRule(r) { await api.del("/api/cloud/alerts/rules/" + r.id); await this.refresh(); },
        async ack(a) { await api.post("/api/cloud/alerts/" + a.id + "/ack"); await this.refresh(); await refreshAlerts(); },
        async snooze(a) { await api.post("/api/cloud/alerts/" + a.id + "/snooze", { hours: 1 }); await this.refresh(); },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.cloud') }}</h2>
      <TabView>
        <TabPanel header="Provisioning">
          <Card v-if="prov"><template #title>Device State</template><template #content>
            <div class="kv"><span class="k">State</span><Tag :value="prov.provisioning.state" /></div>
            <div class="kv"><span class="k">Endpoint</span><span class="v">{{ prov.provisioning.endpoint || '—' }}</span></div>
            <div v-if="can('admin')" class="mt-2">
              <Button label="Advance state" icon="pi pi-arrow-right" @click="advance" class="mr-2" />
              <Button label="Generate claim token" icon="pi pi-key" outlined @click="claim" />
            </div>
          </template></Card>
        </TabPanel>
        <TabPanel header="Telemetry">
          <Card v-if="telemetry"><template #title>Metric Catalog</template><template #content>
            <DataTable :value="telemetry.catalog" size="small">
              <Column field="metric" header="Metric"></Column>
              <Column header="Enabled"><template #body="s"><Tag :severity="s.data.enabled?'success':'secondary'" :value="String(s.data.enabled)" /></template></Column>
              <Column header="Interval"><template #body="s">{{ s.data.interval_s }}s</template></Column>
            </DataTable>
            <div class="kv mt-2"><span class="k">Queue depth</span><span class="v">{{ telemetry.queue_depth }}</span></div>
          </template></Card>
        </TabPanel>
        <TabPanel header="Alerts">
          <Card v-if="can('operator')"><template #title>New Rule</template><template #content>
            <div class="flex gap-2 align-items-center flex-wrap">
              <InputText v-model="newRule.name" placeholder="name" />
              <Dropdown v-model="newRule.metric" :options="metrics" />
              <Dropdown v-model="newRule.condition" :options="conditions" />
              <InputNumber v-model="newRule.threshold" />
              <InputNumber v-model="newRule.duration_s" suffix=" s" />
              <Button label="Add rule" icon="pi pi-plus" @click="createRule" />
            </div>
          </template></Card>
          <DataTable v-if="rules" :value="rules.rules" size="small" class="mt-2">
            <Column field="name" header="Rule"></Column>
            <Column header="Condition"><template #body="s">{{ s.data.metric }} {{ s.data.condition }} {{ s.data.threshold }} ({{ s.data.duration_s }}s)</template></Column>
            <Column field="severity" header="Severity"></Column>
            <Column header=""><template #body="s"><Button v-if="can('operator')" text size="small" icon="pi pi-trash" @click="deleteRule(s.data)" /></template></Column>
          </DataTable>
          <h4>History</h4>
          <DataTable v-if="alerts" :value="alerts.alerts" size="small" scrollable scrollHeight="240px">
            <Column field="fired_at" header="Fired"></Column>
            <Column field="message" header="Message"></Column>
            <Column header="State"><template #body="s"><Tag :severity="s.data.state==='firing'?'danger':'success'" :value="s.data.state" /></template></Column>
            <Column header=""><template #body="s"><span v-if="can('operator') && s.data.state==='firing'">
              <Button text size="small" label="Ack" @click="ack(s.data)" />
              <Button text size="small" label="Snooze" @click="snooze(s.data)" /></span></template></Column>
          </DataTable>
        </TabPanel>
      </TabView>
    </div>
  `,
};

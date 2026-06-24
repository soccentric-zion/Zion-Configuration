// Telemetry metric selection, store-and-forward queue, retention.
import { api } from "../api.js";
import { can } from "../store.js";
import { t } from "../i18n.js";

export default {
    data() {
        return {
            can, catalog: null, selection: [], queue: null,
            retentionDays: 30, lastSync: null,
        };
    },
    async mounted() { await this.refresh(); },
    methods: {
        t,
        async refresh() {
            [this.catalog, this.selection, this.queue, this.lastSync] = await Promise.all([
                api.get("/api/telemetry/catalog"), api.get("/api/telemetry/selection"),
                api.get("/api/telemetry/queue"), api.get("/api/telemetry/last-sync"),
            ]);
        },
        async toggle(metric, on) {
            // The backend PUT /selection takes a single metric config; update
            // the optimistic local copy first so the UI stays responsive.
            const m = (this.selection || []).find((x) => x.name === metric);
            await api.put("/api/telemetry/selection", {
                name: metric, enabled: on,
                interval_s: m ? m.interval_s : 1.0,
                payload_format: m ? m.payload_format : "json",
            });
            await this.refresh();
        },
        async flush() { await api.post("/api/telemetry/queue/flush", { confirm: true }); this.$toast.add({ severity: "success", summary: "Queue flushed" }); await this.refresh(); },
        async setRetention() { await api.put("/api/telemetry/retention", { days: this.retentionDays }); this.$toast.add({ severity: "success", summary: "Retention: " + this.retentionDays + "d" }); },
        isOn(name) { const m = (this.selection || []).find((x) => x.name === name); return m ? !!m.enabled : true; },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.telemetry') }}</h2>
      <div class="grid-cards">
        <Card><template #title><i class="pi pi-chart-line"></i> Metric selection</template>
          <template #content>
            <DataTable v-if="catalog" :value="catalog.metrics" size="small">
              <Column field="name" header="Metric"></Column>
              <Column field="unit" header="Unit"></Column>
              <Column field="default_interval_s" header="Interval (s)"></Column>
              <Column header="Send"><template #body="s">
                <InputSwitch :modelValue="isOn(s.data.name)" @update:modelValue="(v) => toggle(s.data.name, v)" :disabled="!can('admin')" />
              </template></Column>
            </DataTable>
          </template>
        </Card>
        <Card v-if="can('admin')"><template #title><i class="pi pi-database"></i> Retention</template>
          <template #content>
            <InputNumber v-model="retentionDays" :min="1" :max="365" class="mb-2" />
            <Button icon="pi pi-save" label="Apply" @click="setRetention" />
            <Divider />
            <p class="text-muted" v-if="lastSync && lastSync.recorded">Last sync: {{ lastSync.last_sync }}</p>
            <p class="text-muted" v-else>Last sync: never</p>
          </template>
        </Card>
        <Card><template #title><i class="pi pi-inbox"></i> Store-and-forward queue</template>
          <template #content>
            <p v-if="queue">{{ queue.depth || 0 }} pending (capacity {{ queue.capacity || 0 }})</p>
            <p class="text-muted" v-if="queue && queue.oldest_queued_at">oldest: {{ queue.oldest_queued_at }}</p>
            <Button v-if="can('admin')" icon="pi pi-refresh" label="Flush now" @click="flush" />
          </template>
        </Card>
      </div>
    </div>
  `,
};
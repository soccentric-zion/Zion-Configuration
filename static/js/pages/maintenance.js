// Maintenance: db stats, vacuum, log level, cache, diagnostic bundle.
import { api } from "../api.js";
import { can } from "../store.js";
import { t } from "../i18n.js";

export default {
    data() {
        return {
            can, db: null, level: "info",
            levels: ["debug", "info", "warning", "error"],
            timer: null,
        };
    },
    async mounted() {
        await this.refresh();
        this.timer = setInterval(() => this.refresh(true), 10000);
    },
    beforeUnmount() { clearInterval(this.timer); },
    methods: {
        t,
        async refresh(quiet) {
            try { this.db = await api.get("/api/maintenance/db-stats"); } catch (_) {}
        },
        async vacuum() { await api.post("/api/maintenance/vacuum", { confirm: true }); this.$toast.add({ severity: "success", summary: "VACUUM done" }); await this.refresh(); },
        async clearCache() { await api.post("/api/maintenance/clear-cache", { confirm: true }); this.$toast.add({ severity: "success", summary: "Cache cleared" }); },
        async setLevel() { await api.post("/api/maintenance/log-level", { level: this.level, confirm: true }); this.$toast.add({ severity: "success", summary: "Log level set" }); },
        async diag() { window.open("/api/maintenance/diagnostic-bundle", "_blank"); },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.maintenance') }}</h2>
      <div class="grid-cards">
        <Card v-if="db && db.hal"><template #title><i class="pi pi-database"></i> Database</template>
          <template #content>
            <div class="kv"><span class="k">Path</span><span class="v">{{ db.hal.path }}</span></div>
            <div class="kv"><span class="k">Size</span><span class="v">{{ Math.round(db.hal.size_bytes/1024) }} KB</span></div>
            <div class="kv"><span class="k">WAL</span><span class="v">{{ db.hal.wal_mode }}</span></div>
            <div v-if="db.row_counts" class="mt-2">
              <div class="text-muted mb-1">Row counts</div>
              <div class="kv" v-for="(c, k) in db.row_counts" :key="k">
                <span class="k">{{ k }}</span><span class="v">{{ c }}</span>
              </div>
            </div>
            <Button class="mt-2" v-if="can('admin')" icon="pi pi-cog" label="VACUUM" @click="vacuum" />
          </template>
        </Card>
        <Card v-if="can('admin')"><template #title><i class="pi pi-bolt"></i> Log level</template>
          <template #content>
            <Dropdown v-model="level" :options="levels" class="mb-2" />
            <Button icon="pi pi-save" label="Apply" @click="setLevel" />
          </template>
        </Card>
        <Card v-if="can('admin')"><template #title><i class="pi pi-eraser"></i> Cache</template>
          <template #content>
            <p class="text-muted">Invalidate the in-process response cache.</p>
            <Button icon="pi pi-trash" label="Clear cache" @click="clearCache" />
          </template>
        </Card>
        <Card><template #title><i class="pi pi-download"></i> Diagnostic bundle</template>
          <template #content>
            <p class="text-muted">Snapshot of logs, system info, audit, and configuration. Share this when filing an issue.</p>
            <Button icon="pi pi-download" label="Download bundle" @click="diag" />
          </template>
        </Card>
      </div>
    </div>
  `,
};
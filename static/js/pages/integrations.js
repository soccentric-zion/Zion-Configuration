// Integrations: SWUpdate endpoint, cloud link, telemetry endpoint, co-processor defaults.
import { api } from "../api.js";
import { can } from "../store.js";
import { t } from "../i18n.js";

export default {
    data() {
        return { can, list: null, edit: null, draft: null };
    },
    async mounted() { this.list = await api.get("/api/integrations"); },
    methods: {
        t,
        kindLabel(k) {
            return { swupdate: "SWUpdate", cloud: "Cloud", telemetry: "Telemetry", coprocessor: "Co-processor" }[k] || k;
        },
        openEdit(i) {
            this.edit = i;
            this.draft = {
                kind: i.kind, name: i.name, enabled: !!i.enabled,
                url: (i.config && i.config.url) || "", config: i.config || {},
            };
        },
        cancel() { this.edit = null; this.draft = null; },
        async save() {
            await api.post("/api/integrations", { ...this.draft, confirm: true });
            this.$toast.add({ severity: "success", summary: "Saved" });
            this.edit = null; this.draft = null;
            this.list = await api.get("/api/integrations");
        },
        async test(i) {
            const r = await api.post("/api/integrations/test", { kind: i.kind, name: i.name });
            this.$toast.add({ severity: "info", summary: "Test result", detail: r.url || "" });
        },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.integrations') }}</h2>
      <Card><template #title><i class="pi pi-link"></i> Integrations</template>
        <template #content>
          <DataTable v-if="list" :value="list.integrations" size="small">
            <Column field="name" header="Name"></Column>
            <Column field="kind" header="Type"><template #body="s">{{ kindLabel(s.data.kind) }}</template></Column>
            <Column field="config" header="URL"><template #body="s">{{ s.data.config && s.data.config.url }}</template></Column>
            <Column header="Enabled"><template #body="s">
              <Tag :severity="s.data.enabled?'success':'secondary'" :value="String(!!s.data.enabled)" /></template></Column>
            <Column header=""><template #body="s" v-if="can('admin')">
              <Button text size="small" icon="pi pi-pencil" @click="openEdit(s.data)" />
              <Button text size="small" icon="pi pi-send" @click="test(s.data)" />
            </template></Column>
          </DataTable>
        </template>
      </Card>
      <Dialog v-if="edit" :visible="true" @update:visible="(v) => !v && cancel()" modal :header="'Edit: ' + edit.name" :style="{width:'40rem'}">
        <div style="display:grid;grid-template-columns:1fr 2fr;gap:.5rem;align-items:center">
          <label>Name</label><InputText v-model="draft.name" disabled />
          <label>URL</label><InputText v-model="draft.url" />
          <label>Enabled</label><InputSwitch v-model="draft.enabled" />
        </div>
        <template #footer>
          <Button label="Cancel" outlined @click="cancel" />
          <Button label="Save" icon="pi pi-save" @click="save" />
        </template>
      </Dialog>
    </div>
  `,
};
// Per-user IP allowlist + access windows.
import { api } from "../api.js";
import { can } from "../store.js";
import { t } from "../i18n.js";

export default {
    data() {
        return {
            can, list: null, editing: null,
            form: { username: "", ip_allowlist: "", windows: [] },
        };
    },
    async mounted() { await this.refresh(); },
    methods: {
        t,
        async refresh() { this.list = await api.get("/api/access/policy"); },
        edit(u) {
            this.editing = u.username;
            this.form = { username: u.username, ip_allowlist: (u.ip_allowlist || []).join(", "), windows: u.windows || [] };
        },
        cancel() { this.editing = null; },
        async save() {
            const body = {
                username: this.form.username,
                ip_allowlist: (this.form.ip_allowlist || "").split(",").map((s) => s.trim()).filter(Boolean),
                windows: this.form.windows || [],
            };
            await api.put("/api/access/policy", body);
            this.$toast.add({ severity: "success", summary: "Policy saved" });
            this.editing = null;
            await this.refresh();
        },
        addWindow() { this.form.windows.push({ start: "09:00", end: "17:00", days: ["mon", "tue", "wed", "thu", "fri"] }); },
        delWindow(i) { this.form.windows.splice(i, 1); },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.access') }}</h2>
      <Card><template #title>Per-user access policy</template>
        <template #content>
          <DataTable v-if="list" :value="list.policies" size="small">
            <Column field="username" header="User"></Column>
            <Column field="ip_allowlist" header="IP allowlist"><template #body="s">
              <span class="text-muted">{{ (s.data.ip_allowlist||[]).join(', ') || 'any' }}</span>
            </template></Column>
            <Column field="windows" header="Windows"><template #body="s">
              <Tag v-for="(w,i) in (s.data.windows||[])" :key="i" :value="w.start + '–' + w.end" class="mr-1" />
            </template></Column>
            <Column header=""><template #body="s" v-if="can('admin')">
              <Button text size="small" icon="pi pi-pencil" @click="edit(s.data)" />
            </template></Column>
          </DataTable>
        </template>
      </Card>
      <Dialog v-if="editing" :visible="true" @update:visible="(v) => !v && cancel()" modal :header="'Edit policy: ' + editing" :style="{width:'40rem'}">
        <div class="grid">
          <label>Username</label><InputText v-model="form.username" disabled />
          <label>IP allowlist</label><InputText v-model="form.ip_allowlist" placeholder="10.0.0.0/8, 192.168.1.0/24" />
        </div>
        <Divider />
        <div v-for="(w,i) in form.windows" :key="i" class="flex gap-2 align-items-center mb-2">
          <InputText v-model="w.start" placeholder="09:00" />
          <InputText v-model="w.end" placeholder="17:00" />
          <Button text icon="pi pi-trash" severity="danger" @click="delWindow(i)" />
        </div>
        <Button size="small" icon="pi pi-plus" label="Add window" @click="addWindow" />
        <template #footer>
          <Button label="Cancel" outlined @click="cancel" />
          <Button label="Save" icon="pi pi-save" @click="save" />
        </template>
      </Dialog>
    </div>
  `,
};

// Custom roles + role-permission matrix.
import { api } from "../api.js";
import { can } from "../store.js";
import { t } from "../i18n.js";

export default {
    data() {
        return { can, list: null, matrix: null, newRole: { name: "", rank: 1, permissions: {} } };
    },
    async mounted() { await this.refresh(); },
    methods: {
        t,
        async refresh() {
            [this.list, this.matrix] = await Promise.all([
                api.get("/api/roles"), api.get("/api/roles/matrix"),
            ]);
        },
        async add() {
            await api.post("/api/roles", this.newRole);
            this.newRole = { name: "", rank: 1, permissions: {} };
            this.$toast.add({ severity: "success", summary: "Role added" });
            await this.refresh();
        },
        async del(r) {
            this.$confirm.require({
                message: "Delete role '" + r.name + "'?", header: "Confirm", icon: "pi pi-trash",
                accept: async () => { await api.del("/api/roles/" + r.id); await this.refresh(); }
            });
        },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.roles') }}</h2>
      <div class="grid-cards">
        <Card><template #title>Roles</template>
          <template #content>
            <DataTable v-if="list" :value="list.roles" size="small">
              <Column field="name" header="Name"></Column>
              <Column field="rank" header="Rank"></Column>
              <Column header=""><template #body="s" v-if="can('admin')">
                <Button text size="small" icon="pi pi-trash" severity="danger" @click="del(s.data)" />
              </template></Column>
            </DataTable>
          </template>
        </Card>
        <Card v-if="can('admin')"><template #title>Add role</template>
          <template #content>
            <div style="display:grid;grid-template-columns:2fr 1fr auto;gap:.5rem;align-items:end">
              <span><label>Name</label><InputText v-model="newRole.name" /></span>
              <span><label>Rank</label><InputNumber v-model="newRole.rank" :min="0" :max="10" /></span>
              <Button label="Add" icon="pi pi-plus" :disabled="!newRole.name" @click="add" />
            </div>
          </template>
        </Card>
        <Card class="col-span-2"><template #title>Permission matrix</template>
          <template #content>
            <DataTable v-if="matrix" :value="matrix.matrix" size="small">
              <Column field="role" header="Role"></Column>
              <Column field="permissions" header="Permissions"><template #body="s">
                <Tag v-for="(v,k) in s.data.permissions" :key="k" :value="k + ':' + (Array.isArray(v)?v.join(','):v)" class="mr-1" />
              </template></Column>
            </DataTable>
          </template>
        </Card>
      </div>
    </div>
  `,
};

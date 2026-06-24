// Group CRUD + nested group memberships.
import { api } from "../api.js";
import { can } from "../store.js";
import { t } from "../i18n.js";

export default {
    data() {
        return { can, list: null, newGroup: { name: "", description: "", parent_id: null }, newMember: {} };
    },
    async mounted() { this.list = await api.get("/api/groups"); },
    methods: {
        t,
        async add() {
            await api.post("/api/groups", this.newGroup);
            this.newGroup = { name: "", description: "", parent_id: null };
            this.$toast.add({ severity: "success", summary: "Group added" });
            this.list = await api.get("/api/groups");
        },
        async del(g) {
            this.$confirm.require({
                message: "Delete group '" + g.name + "'?", header: "Confirm", icon: "pi pi-trash",
                accept: async () => { await api.del("/api/groups/" + g.id); this.list = await api.get("/api/groups"); }
            });
        },
        async addMember(g) {
            const username = (this.newMember[g.id] || "").trim();
            if (!username) return;
            await api.post("/api/groups/" + g.id + "/members", { username });
            this.newMember[g.id] = "";
            this.list = await api.get("/api/groups");
        },
        async delMember(g, username) {
            await api.del("/api/groups/" + g.id + "/members/" + username);
            this.list = await api.get("/api/groups");
        },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.groups') }}</h2>
      <Card v-if="can('admin')" class="mb-3"><template #title>Add group</template>
        <template #content>
          <div style="display:grid;grid-template-columns:2fr 3fr 1fr auto;gap:.5rem;align-items:end">
            <span><label>Name</label><InputText v-model="newGroup.name" /></span>
            <span><label>Description</label><InputText v-model="newGroup.description" /></span>
            <span><label>Parent</label><InputNumber v-model="newGroup.parent_id" :useGrouping="false" /></span>
            <Button label="Add" icon="pi pi-plus" :disabled="!newGroup.name" @click="add" />
          </div>
        </template>
      </Card>
      <Card v-for="g in (list ? (list.groups || list.dashboard) : [])" :key="g.id" class="mb-2">
        <template #title>
          <i class="pi pi-users"></i> {{ g.name }}
          <span class="text-muted ml-2">{{ g.description }}</span>
        </template>
        <template #content>
          <div class="flex gap-2 align-items-center mb-2" v-if="can('admin')">
            <InputText v-model="newMember[g.id]" placeholder="add username" />
            <Button size="small" icon="pi pi-user-plus" label="Add member" @click="addMember(g)" />
            <Button size="small" icon="pi pi-trash" severity="danger" outlined @click="del(g)" />
          </div>
          <div>
            <Tag v-for="u in g.members || []" :key="u" class="mr-1 mb-1" :value="u">
              <i class="pi pi-times" style="margin-left:.5rem;cursor:pointer" @click="delMember(g, u)" v-if="can('admin')"></i>
            </Tag>
            <span v-if="!(g.members||[]).length" class="text-muted">no members</span>
          </div>
        </template>
      </Card>
    </div>
  `,
};

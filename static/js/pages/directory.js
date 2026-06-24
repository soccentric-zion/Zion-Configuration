// Directory providers (LDAP / AD / SSO).
import { api } from "../api.js";
import { can } from "../store.js";
import { t } from "../i18n.js";

export default {
    data() {
        return {
            can, list: null,
            newP: {
                provider: "ldap", name: "", enabled: false,
                host: "", port: 389, base_dn: "", bind_dn: "", bind_password: "",
                use_tls: true, group_filter: "",
                client_id: "", client_secret: "", authorize_url: "", token_url: "",
            },
        };
    },
    async mounted() { this.list = await api.get("/api/directory"); },
    methods: {
        t,
        kindLabel(k) {
            return { ldap: "LDAP", active_directory: "Active Directory", sso_oauth: "SSO (OAuth)", sso_saml: "SSO (SAML)" }[k] || k;
        },
        async add() {
            await api.post("/api/directory", this.newP);
            this.newP = {
                provider: "ldap", name: "", enabled: false,
                host: "", port: 389, base_dn: "", bind_dn: "", bind_password: "",
                use_tls: true, group_filter: "",
                client_id: "", client_secret: "", authorize_url: "", token_url: "",
            };
            this.$toast.add({ severity: "success", summary: "Provider added" });
            this.list = await api.get("/api/directory");
        },
        async del(p) {
            this.$confirm.require({
                message: "Delete provider '" + p.name + "'?", header: "Confirm", icon: "pi pi-trash",
                accept: async () => { await api.del("/api/directory/" + p.id); this.list = await api.get("/api/directory"); }
            });
        },
        async sync() {
            const r = await api.post("/api/directory/sync", { confirm: true });
            this.$toast.add({ severity: "info", summary: "Sync started", detail: r.detail || "" });
        },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.directory') }}</h2>
      <Card class="mb-3" v-if="can('admin')"><template #title>Add provider</template>
        <template #content>
          <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:.5rem;align-items:end">
            <span><label>Type</label><Dropdown v-model="newP.provider" :options="['ldap','active_directory','sso_oauth','sso_saml']" /></span>
            <span><label>Name</label><InputText v-model="newP.name" /></span>
            <span v-if="newP.provider !== 'sso_saml'"><label>Host</label><InputText v-model="newP.host" /></span>
            <span v-if="newP.provider !== 'sso_saml'"><label>Port</label><InputNumber v-model="newP.port" :useGrouping="false" /></span>
            <span><label>Base DN</label><InputText v-model="newP.base_dn" /></span>
            <span><label>Bind DN</label><InputText v-model="newP.bind_dn" /></span>
            <span v-if="newP.provider === 'sso_oauth'"><label>Client ID</label><InputText v-model="newP.client_id" /></span>
            <span v-if="newP.provider === 'sso_oauth'"><label>Client secret</label><InputText type="password" v-model="newP.client_secret" /></span>
            <span v-if="newP.provider === 'sso_saml'"><label>Authorize URL</label><InputText v-model="newP.authorize_url" /></span>
            <span v-if="newP.provider === 'sso_saml'"><label>Token URL</label><InputText v-model="newP.token_url" /></span>
          </div>
          <Button class="mt-2" icon="pi pi-plus" label="Add" @click="add" />
        </template>
      </Card>
      <Card><template #title>Providers</template>
        <template #content>
          <DataTable v-if="list" :value="list.providers" size="small">
            <Column field="name" header="Name"></Column>
            <Column field="provider" header="Type"><template #body="s">{{ kindLabel(s.data.provider) }}</template></Column>
            <Column field="host" header="Host"></Column>
            <Column header="Enabled"><template #body="s">
              <Tag :severity="s.data.enabled?'success':'secondary'" :value="String(!!s.data.enabled)" /></template></Column>
            <Column header=""><template #body="s" v-if="can('admin')">
              <Button text size="small" icon="pi pi-refresh" label="Sync" @click="sync" />
              <Button text size="small" icon="pi pi-trash" severity="danger" @click="del(s.data)" />
            </template></Column>
          </DataTable>
        </template>
      </Card>
    </div>
  `,
};

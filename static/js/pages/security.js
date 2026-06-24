import { api } from "../api.js";
import { can } from "../store.js";
import { t } from "../i18n.js";

export default {
    data() {
        return {
            users: null, sshKeys: null, certs: null, audit: null, compliance: null,
            sbom: null, cve: null, secureBoot: null, fuses: null, enrolledKeys: null, tpm: null,
            newUser: { username: "", password: "", role: "viewer" },
            roles: ["admin", "operator", "viewer"], can
        };
    },
    async mounted() { await this.refresh(); },
    methods: {
        t,
        async refresh() {
            const calls = [api.get("/api/security/ssh-keys"), api.get("/api/security/certificates"),
            api.get("/api/security/audit"), api.get("/api/security/compliance"),
            api.get("/api/security/sbom"), api.get("/api/security/cve"),
            api.get("/api/security/secure-boot"), api.get("/api/security/fuses"),
            api.get("/api/security/keys/enrolled"), api.get("/api/security/tpm")];
            if (can("admin")) calls.unshift(api.get("/api/users"));
            const res = await Promise.all(calls);
            if (can("admin")) { this.users = res.shift(); }
            [this.sshKeys, this.certs, this.audit, this.compliance, this.sbom, this.cve,
             this.secureBoot, this.fuses, this.enrolledKeys, this.tpm] = res;
        },
        async createUser() {
            await api.post("/api/users", this.newUser);
            this.$toast.add({ severity: "success", summary: "User created", life: 1500 });
            this.newUser = { username: "", password: "", role: "viewer" };
            await this.refresh();
        },
        deleteUser(u) {
            this.$confirm.require({
                message: "Delete user " + u.username + "?", header: "Confirm", icon: "pi pi-trash",
                acceptClass: "p-button-danger",
                accept: async () => { await api.del("/api/users/" + u.username); await this.refresh(); }
            });
        },
        async setUserSource(u, userSource) {
            // Switching to 'system' requires a system_username — use the
            // dashboard username as a sensible default; the operator
            // can edit it via the inline editor.
            const body = { user_source: userSource };
            if (userSource === "system" && !u.system_username) {
                body.system_username = u.username;
            }
            await api.patch("/api/users/" + u.username + "/source", body);
            this.$toast.add({ severity: "success", summary: u.username + " → " + userSource, life: 1500 });
            await this.refresh();
        },
        async saveSystemUsername(u) {
            await api.patch("/api/users/" + u.username + "/source", {
                user_source: u.user_source,
                system_username: u.system_username,
            });
            this.$toast.add({ severity: "success", summary: "System username updated", life: 1500 });
            await this.refresh();
        },
        exportAudit() { window.open("/api/security/audit/export", "_blank"); },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.security') }}</h2>
      <TabView>
        <TabPanel header="Users" v-if="can('admin')">
          <DataTable v-if="users" :value="users" size="small">
            <Column field="username" header="User"></Column>
            <Column field="role" header="Role"></Column>
            <Column header="Source"><template #body="s">
              <SelectButton :options="['local','system']" v-model="s.data.user_source"
                            @update:modelValue="setUserSource(s.data, $event)" />
            </template></Column>
            <Column header="System user"><template #body="s">
              <InputText v-if="s.data.user_source === 'system'" v-model="s.data.system_username"
                         size="small" placeholder="PAM user"
                         @blur="saveSystemUsername(s.data)" />
              <span v-else class="v muted">—</span>
            </template></Column>
            <Column header="Enabled"><template #body="s"><Tag :severity="s.data.enabled?'success':'secondary'" :value="String(s.data.enabled)" /></template></Column>
            <Column header=""><template #body="s"><Button text size="small" icon="pi pi-trash" @click="deleteUser(s.data)" /></template></Column>
          </DataTable>
          <Divider />
          <div class="flex gap-2 align-items-center">
            <InputText v-model="newUser.username" placeholder="username" />
            <InputText v-model="newUser.password" placeholder="password" type="password" />
            <Dropdown v-model="newUser.role" :options="roles" />
            <Button label="Add user" icon="pi pi-plus" @click="createUser" />
          </div>
        </TabPanel>
        <TabPanel header="SSH Keys">
          <DataTable v-if="sshKeys" :value="sshKeys.keys" size="small">
            <Column field="comment" header="Comment"></Column>
            <Column field="fingerprint" header="Fingerprint"></Column>
          </DataTable>
        </TabPanel>
        <TabPanel header="Certificates">
          <DataTable v-if="certs" :value="certs.certificates" size="small">
            <Column field="subject" header="Subject"></Column>
            <Column field="expires" header="Expires"></Column>
            <Column header="Days"><template #body="s">
              <Tag :severity="s.data.days_remaining<30?'danger':'success'" :value="String(s.data.days_remaining)" /></template></Column>
          </DataTable>
        </TabPanel>
        <TabPanel header="Secure Boot">
          <div class="readonly-badge" data-testid="readonly-badge-secure-boot" role="note" aria-label="Read-only">
            <Tag severity="info" value="Status only — changes must be made in firmware provisioning" />
          </div>
          <pre v-if="secureBoot" style="font-size:.8rem">{{ JSON.stringify(secureBoot, null, 2) }}</pre>
        </TabPanel>
        <TabPanel header="Fuses">
          <div class="readonly-badge" data-testid="readonly-badge-fuses" role="note" aria-label="Read-only">
            <Tag severity="info" value="Status only — fuses are written at factory provisioning" />
          </div>
          <pre v-if="fuses" style="font-size:.8rem">{{ JSON.stringify(fuses, null, 2) }}</pre>
        </TabPanel>
        <TabPanel header="Enrolled Keys">
          <div class="readonly-badge" data-testid="readonly-badge-enrolled-keys" role="note" aria-label="Read-only">
            <Tag severity="info" value="Status only — key enrollment happens at OEM provisioning" />
          </div>
          <pre v-if="enrolledKeys" style="font-size:.8rem">{{ JSON.stringify(enrolledKeys, null, 2) }}</pre>
        </TabPanel>
        <TabPanel header="TPM">
          <div class="readonly-badge" data-testid="readonly-badge-tpm" role="note" aria-label="Read-only">
            <Tag severity="info" value="Status only — TPM sealed-state is set at provisioning" />
          </div>
          <pre v-if="tpm" style="font-size:.8rem">{{ JSON.stringify(tpm, null, 2) }}</pre>
        </TabPanel>
        <TabPanel header="Audit Trail">
          <Button label="Export CSV" icon="pi pi-download" text @click="exportAudit" class="mb-2" />
          <DataTable v-if="audit" :value="audit.entries" size="small" scrollable scrollHeight="380px">
            <Column field="ts" header="Time"></Column>
            <Column field="username" header="User"></Column>
            <Column field="ip" header="IP"></Column>
            <Column field="action" header="Action"></Column>
            <Column field="target" header="Target"></Column>
          </DataTable>
        </TabPanel>
        <TabPanel header="Compliance / SBOM">
          <Card><template #title>Compliance</template><template #content>
            <pre v-if="compliance" style="font-size:.8rem">{{ JSON.stringify(compliance.compliance, null, 2) }}</pre>
          </template></Card>
          <Card class="mt-2"><template #title>SBOM ({{ sbom && sbom.sbom.bomFormat }})</template><template #content>
            <DataTable v-if="sbom" :value="sbom.sbom.components" size="small" scrollable scrollHeight="200px">
              <Column field="name" header="Component"></Column><Column field="version" header="Version"></Column>
            </DataTable>
          </template></Card>
          <Card class="mt-2"><template #title>CVE Scan</template><template #content>
            <div v-if="cve && !cve.available">No scan available.</div>
            <DataTable v-else-if="cve" :value="cve.matches" size="small"><Column field="id" header="CVE"></Column></DataTable>
          </template></Card>
        </TabPanel>
      </TabView>
    </div>
  `,
};

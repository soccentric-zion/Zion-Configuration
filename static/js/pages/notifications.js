// Notification channels (email / webhook / toast) + per-event toggles.
import { api } from "../api.js";
import { can } from "../store.js";
import { t } from "../i18n.js";

export default {
    data() {
        return {
            can, channels: null, classes: null,
            newCh: {
                channel: "email", name: "", enabled: true,
                smtp_host: "", smtp_port: 587, smtp_user: "", smtp_password: "",
                smtp_from: "", smtp_to: "", smtp_use_tls: true,
                webhook_url: "", events: {},
            },
        };
    },
    async mounted() { await this.refresh(); },
    methods: {
        t,
        async refresh() {
            [this.channels, this.classes] = await Promise.all([
                api.get("/api/notifications/channels"),
                api.get("/api/notifications/event-classes"),
            ]);
        },
        toList(s) { return (s || "").split(",").map((x) => x.trim()).filter(Boolean); },
        async save() {
            if (this.newCh.channel === "email") {
                this.newCh.smtp_to = this.toList(this.newCh.smtp_to);
            }
            await api.post("/api/notifications/channels", this.newCh);
            this.$toast.add({ severity: "success", summary: "Channel saved", life: 1500 });
            this.newCh = {
                channel: "email", name: "", enabled: true,
                smtp_host: "", smtp_port: 587, smtp_user: "", smtp_password: "",
                smtp_from: "", smtp_to: "", smtp_use_tls: true,
                webhook_url: "", events: {},
            };
            await this.refresh();
        },
        async del(c) {
            this.$confirm.require({
                message: "Delete channel '" + c.name + "'?", header: "Confirm", icon: "pi pi-trash",
                accept: async () => { await api.del("/api/notifications/channels/" + c.id); await this.refresh(); }
            });
        },
        async test(c) { await api.post("/api/notifications/test-send", { channel_id: c.id }); this.$toast.add({ severity: "info", summary: "Test sent (simulated)" }); },
        eventKey(c, cls) { return c + ":" + cls; },
        isChecked(c, cls) { return !!(c.events && c.events[cls]); },
        parseConfig(c) { try { return c.config_json ? JSON.parse(c.config_json) : {}; } catch (_) { return {}; } },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.notifications') }}</h2>
      <div class="grid-cards">
        <Card><template #title><i class="pi pi-bell"></i> Channels</template>
          <template #content>
            <DataTable v-if="channels" :value="channels.channels" size="small">
              <Column field="name" header="Name"></Column>
              <Column field="channel" header="Type"></Column>
              <Column header="Enabled"><template #body="s">
                <Tag :severity="s.data.enabled?'success':'secondary'" :value="String(!!s.data.enabled)" /></template></Column>
              <Column header="Events"><template #body="s">
                <Tag v-for="ev in (s.data.events_json ? Object.keys(JSON.parse(s.data.events_json)) : [])" :key="ev" :value="ev" class="mr-1" /></template></Column>
              <Column header=""><template #body="s" v-if="can('admin')">
                <Button text size="small" icon="pi pi-send" @click="test(s.data)" />
                <Button text size="small" icon="pi pi-trash" severity="danger" @click="del(s.data)" />
              </template></Column>
            </DataTable>
          </template>
        </Card>
        <Card v-if="can('admin')"><template #title>Add channel</template>
          <template #content>
            <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:.5rem;align-items:end">
              <span><label>Type</label><Dropdown v-model="newCh.channel" :options="['email','webhook','toast']" /></span>
              <span><label>Name</label><InputText v-model="newCh.name" /></span>
              <template v-if="newCh.channel === 'email'">
                <span><label>SMTP host</label><InputText v-model="newCh.smtp_host" /></span>
                <span><label>SMTP port</label><InputNumber v-model="newCh.smtp_port" :useGrouping="false" /></span>
                <span><label>User</label><InputText v-model="newCh.smtp_user" /></span>
                <span><label>Password</label><InputText type="password" v-model="newCh.smtp_password" /></span>
                <span><label>From</label><InputText v-model="newCh.smtp_from" /></span>
                <span><label>To (comma-separated)</label><InputText v-model="newCh.smtp_to" /></span>
                <span><label>TLS</label><InputSwitch v-model="newCh.smtp_use_tls" /></span>
              </template>
              <template v-if="newCh.channel === 'webhook'">
                <span style="grid-column:span 2"><label>Webhook URL</label><InputText v-model="newCh.webhook_url" /></span>
              </template>
            </div>
            <div v-if="classes" class="mt-2">
              <label class="block mb-1">Events</label>
              <div style="display:flex;gap:1rem;flex-wrap:wrap">
                <label v-for="c in classes.classes" :key="c">
                  <InputSwitch v-model="newCh.events[c]" /> <span style="margin-left:.5rem">{{ c }}</span>
                </label>
              </div>
            </div>
            <Button class="mt-3" icon="pi pi-save" label="Save" @click="save" />
          </template>
        </Card>
      </div>
    </div>
  `,
};

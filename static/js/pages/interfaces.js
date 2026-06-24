import { api } from "../api.js";
import { can } from "../store.js";
import { t } from "../i18n.js";

export default {
    data() {
        return {
            interfaces: [], devices: [], simulated: false, can,
            newDev: { name: "", kind: "sensor", interface: "i2c-1", address: "0x48" },
            opResult: null, timer: null,
        };
    },
    async mounted() {
        await this.refresh();
        this.timer = setInterval(() => this.refresh(true), 3000);
    },
    beforeUnmount() { clearInterval(this.timer); },
    methods: {
        t,
        async refresh(quiet) {
            const [ifaces, devs] = await Promise.all([
                api.get("/api/interfaces"),
                api.get("/api/devices"),
            ]);
            this.interfaces = ifaces.interfaces; this.simulated = ifaces.simulated;
            this.devices = devs.devices;
            if (!quiet) this.opResult = null;
        },
        async toggle(iface) {
            const r = await api.post(`/api/interfaces/${iface.name}/enable`, { enabled: !iface.enabled });
            iface.enabled = r.enabled;
            this.$toast.add({ severity: "success", summary: `${iface.name} ${r.enabled ? "enabled" : "disabled"}`, life: 1500 });
        },
        async saveConfig(iface) {
            await api.post(`/api/interfaces/${iface.name}/config`, iface.config);
            this.$toast.add({ severity: "success", summary: `${iface.name} configured`, life: 1500 });
        },
        async operate(iface, op, params) {
            this.opResult = await api.post(`/api/interfaces/${iface.name}/operate`, { op, params: params || {} });
            this.$toast.add({ severity: "info", summary: `${iface.name}: ${op}`, life: 1500 });
        },
        async addDevice() {
            if (!this.newDev.name) return;
            await api.post("/api/devices", this.newDev);
            this.newDev = { name: "", kind: "sensor", interface: "i2c-1", address: "" };
            await this.refresh();
        },
        toggleDevice(d) {
            api.post(`/api/devices/${d.id}/enable`, { enabled: !d.enabled }).then((r) => { d.enabled = r.enabled; });
        },
        delDevice(d) {
            this.$confirm.require({
                message: `Delete device '${d.name}'?`, header: "Confirm", icon: "pi pi-exclamation-triangle",
                accept: async () => { await api.del(`/api/devices/${d.id}`); await this.refresh(); },
            });
        },
        firstConfigKeys(cfg) { return Object.keys(cfg || {}); },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.interfaces') }}
        <Tag v-if="simulated" severity="warning" :value="t('common.simulated')" icon="pi pi-desktop" class="ml-2" />
      </h2>
      <TabView>
        <TabPanel header="Interfaces">
          <div class="grid-cards">
            <Card v-for="iface in interfaces" :key="iface.name">
              <template #title>
                <i class="pi pi-sliders-h"></i> {{ iface.name }}
                <Tag class="ml-2" :value="iface.kind" severity="info" />
                <Tag class="ml-2" :severity="iface.enabled ? 'success' : 'secondary'" :value="iface.enabled ? 'enabled' : 'disabled'" />
              </template>
              <template #content>
                <div class="kv" v-for="k in firstConfigKeys(iface.config)" :key="k">
                  <span class="k">{{ k }}</span>
                  <span class="v" v-if="!can('operator')">{{ iface.config[k] }}</span>
                  <InputText v-else v-model="iface.config[k]" size="small" style="max-width:140px" />
                </div>
                <div v-if="iface.enabled && iface.status" class="kv">
                  <span class="k">status</span>
                  <span class="v">{{ iface.status.state || iface.status.link || 'up' }}</span>
                </div>
                <div class="mt-2" v-if="can('operator')">
                  <Button size="small" :label="iface.enabled ? 'Disable' : 'Enable'"
                          :severity="iface.enabled ? 'warning' : 'success'" outlined class="mr-2"
                          :data-testid="'iface-toggle-'+iface.name" @click="toggle(iface)" />
                  <Button size="small" label="Save config" icon="pi pi-save" text class="mr-2" @click="saveConfig(iface)" />
                  <Button v-if="['uart','rs485','rs422'].includes(iface.kind)" size="small" label="Send" text
                          @click="operate(iface, iface.kind==='uart' ? 'write' : 'send', { data: 'PING', frame: 'PING' })" />
                  <Button v-if="['can','canfd'].includes(iface.kind)" size="small" label="Send frame" text
                          @click="operate(iface, 'send', { id: '0x123', dlc: 8 })" />
                  <Button v-if="iface.kind==='onewire'" size="small" label="Read" text
                          @click="operate(iface, 'read', { address: (iface.status.devices||['28-0'])[0] })" />
                  <Button v-if="iface.kind==='modbus'" size="small" label="Read regs" text
                          @click="operate(iface, 'read', { address: 0, count: 4 })" />
                </div>
              </template>
            </Card>
          </div>
          <Card v-if="opResult" class="mt-3"><template #title>Last operation</template>
            <template #content><pre style="white-space:pre-wrap">{{ JSON.stringify(opResult, null, 2) }}</pre></template></Card>
        </TabPanel>

        <TabPanel header="Devices">
          <Card class="mb-3" v-if="can('operator')"><template #title>Define a device</template>
            <template #content>
              <div class="p-fluid" style="display:grid;grid-template-columns:repeat(5,1fr);gap:.5rem;align-items:end">
                <span><label>Name</label><InputText v-model="newDev.name" data-testid="dev-name" /></span>
                <span><label>Kind</label><InputText v-model="newDev.kind" /></span>
                <span><label>Interface</label><InputText v-model="newDev.interface" /></span>
                <span><label>Address</label><InputText v-model="newDev.address" /></span>
                <Button label="Add device" icon="pi pi-plus" data-testid="dev-add" @click="addDevice" />
              </div>
            </template>
          </Card>
          <DataTable :value="devices" size="small" data-testid="devices-table">
            <Column field="name" header="Name"></Column>
            <Column field="kind" header="Kind"></Column>
            <Column field="interface" header="Interface"></Column>
            <Column field="address" header="Address"></Column>
            <Column header="Enabled"><template #body="s">
              <Tag :severity="s.data.enabled ? 'success':'secondary'" :value="String(s.data.enabled)" /></template></Column>
            <Column header="Actions"><template #body="s">
              <Button v-if="can('operator')" size="small" text :label="s.data.enabled?'Disable':'Enable'" @click="toggleDevice(s.data)" />
              <Button v-if="can('operator')" size="small" text severity="danger" icon="pi pi-trash" @click="delDevice(s.data)" />
            </template></Column>
          </DataTable>
        </TabPanel>
      </TabView>
    </div>
  `,
};

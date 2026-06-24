import { api } from "../api.js";
import { can } from "../store.js";
import { t } from "../i18n.js";

export default {
    data() {
        return {
            can, status: null, platform: null, pcie: null, emmc: null, gpio: null, rtc: null, pmic: null,
            network: [], ports: [], firewall: { rules: [], ruleset: "" }, services: [], wifi: [], bt: null, devices: [],
            newRule: { action: "allow", port: 8080, protocol: "tcp", source: "any" },
            timer: null,
        };
    },
    async mounted() { await this.refresh(); this.timer = setInterval(() => this.refresh(true), 4000); },
    beforeUnmount() { clearInterval(this.timer); },
    methods: {
        t,
        async refresh(quiet) {
            const [status, platform, network, ports, fw, services, devices] = await Promise.all([
                api.get("/api/sysadmin/status"), api.get("/api/sysadmin/platform"),
                api.get("/api/sysadmin/network"), api.get("/api/sysadmin/ports"),
                api.get("/api/sysadmin/firewall"), api.get("/api/sysadmin/services"),
                api.get("/api/sysadmin/devices"),
            ]);
            this.status = status; this.platform = platform; this.network = network.interfaces;
            this.ports = ports.ports || ports.listening || []; this.firewall = fw;
            this.services = services.units; this.devices = devices.devices;
            if (!quiet && platform.features) await this.loadPlatformDetail(platform.features);
        },
        async loadPlatformDetail(features) {
            const calls = [];
            if (features.pcie) calls.push(["pcie", api.get("/api/sysadmin/pcie")]);
            if (features.emmc) calls.push(["emmc", api.get("/api/sysadmin/emmc")]);
            if (features.gpio_banks) calls.push(["gpio", api.get("/api/sysadmin/gpio-banks")]);
            if (features.rtc) calls.push(["rtc", api.get("/api/sysadmin/rtc")]);
            if (features.pmic) calls.push(["pmic", api.get("/api/sysadmin/pmic")]);
            const results = await Promise.all(calls.map((c) => c[1]));
            calls.forEach((c, i) => { this[c[0]] = results[i]; });
        },
        confirmPost(message, url, body) {
            this.$confirm.require({
                message, header: "Confirm", icon: "pi pi-exclamation-triangle",
                accept: async () => { await api.post(url, { ...(body || {}), confirm: true }); await this.refresh(); },
            });
        },
        netLink(iface) {
            if (!iface.up) { api.post(`/api/sysadmin/network/${iface.name}/link`, { up: true }).then(() => this.refresh()); return; }
            this.confirmPost(`Bring ${iface.name} DOWN? You may lose connectivity.`, `/api/sysadmin/network/${iface.name}/link`, { up: false });
        },
        async svcAction(s, action) {
            if (["stop", "disable", "restart"].includes(action)) {
                this.confirmPost(`${action} service '${s.name}'?`, `/api/sysadmin/services/${s.name}/${action}`);
            } else {
                await api.post(`/api/sysadmin/services/${s.name}/${action}`); await this.refresh();
            }
        },
        closePort(p) { this.confirmPost(`Close port ${p.port} (${p.service || p.process || ''})?`, `/api/sysadmin/ports/${p.port}/close`); },
        addRule() {
            this.confirmPost(`Add firewall rule ${this.newRule.action} ${this.newRule.port}/${this.newRule.protocol}?`, "/api/sysadmin/firewall", this.newRule);
        },
        toggleRule(r) { this.confirmPost(`${r.enabled ? "Disable" : "Enable"} rule #${r.id}?`, `/api/sysadmin/firewall/${r.id}/enable`, { enabled: !r.enabled }); },
        delRule(r) {
            this.$confirm.require({
                message: `Delete firewall rule #${r.id}?`, header: "Confirm", icon: "pi pi-exclamation-triangle",
                accept: async () => { await api.del(`/api/sysadmin/firewall/${r.id}?confirm=true`); await this.refresh(); },
            });
        },
        async scanWifi() { this.wifi = (await api.get("/api/sysadmin/wifi/scan")).networks; },
        async loadBt() { this.bt = await api.get("/api/sysadmin/bluetooth"); },
        objKeys(o, skip) { return o ? Object.keys(o).filter((k) => !(skip || ["simulated"]).includes(k)) : []; },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.sysadmin') }}
        <Tag v-if="status && status.simulated" severity="warning" :value="t('common.simulated')" icon="pi pi-desktop" class="ml-2" />
      </h2>
      <TabView @tab-change="(e) => { if (e.index===6) { scanWifi(); loadBt(); } }">
        <TabPanel header="Status">
          <div class="grid-cards" v-if="status">
            <Card><template #title>Services</template><template #content>
              <div class="big-stat">{{ status.services_running }}/{{ status.services_total }}</div><div class="text-muted">running</div></template></Card>
            <Card><template #title>Firewall</template><template #content>
              <div class="big-stat">{{ status.firewall_rules_active }}</div><div class="text-muted">active rules</div></template></Card>
            <Card v-for="k in objKeys(status, ['simulated','services_running','services_total','firewall_rules_active'])" :key="k">
              <template #title>{{ k }}</template><template #content><div class="big-stat">{{ status[k] }}</div></template></Card>
          </div>
        </TabPanel>

        <TabPanel header="Platform (CM5)">
          <Card v-if="platform"><template #title><i class="pi pi-server"></i> {{ platform.model }}
            <Tag class="ml-2" :severity="platform.is_cm5?'success':'warning'" :value="platform.is_cm5?'CM5':'non-CM5'" /></template>
            <template #content>
              <div class="kv"><span class="k">SoC</span><span class="v">{{ platform.soc }}</span></div>
              <div class="kv"><span class="k">carrier</span><span class="v">{{ platform.carrier }}</span></div>
              <div class="kv" v-for="k in objKeys(platform.variant)" :key="k"><span class="k">{{ k }}</span><span class="v">{{ platform.variant[k] }}</span></div>
            </template></Card>
          <div class="grid-cards mt-3">
            <Card v-if="pcie && pcie.present"><template #title>PCIe</template><template #content>
              <div class="kv"><span class="k">lanes</span><span class="v">{{ pcie.lanes }}</span></div>
              <div v-for="d in pcie.devices" :key="d.slot" class="kv"><span class="k">{{ d.slot }}</span><span class="v">{{ d.device }} ({{ d.link }})</span></div></template></Card>
            <Card v-if="emmc && emmc.present"><template #title>eMMC</template><template #content>
              <div class="kv" v-for="k in objKeys(emmc,['simulated','present'])" :key="k"><span class="k">{{ k }}</span><span class="v">{{ emmc[k] }}</span></div></template></Card>
            <Card v-if="gpio"><template #title>GPIO banks</template><template #content>
              <div class="kv" v-for="b in gpio.banks" :key="b.bank"><span class="k">bank {{ b.bank }}</span><span class="v">{{ b.pins }} pins — {{ b.label }}</span></div></template></Card>
            <Card v-if="rtc && rtc.present"><template #title>RTC + battery</template><template #content>
              <div class="kv" v-for="k in objKeys(rtc,['simulated','present'])" :key="k"><span class="k">{{ k }}</span><span class="v">{{ rtc[k] }}</span></div></template></Card>
            <Card v-if="pmic && pmic.present"><template #title>PMIC {{ pmic.chip }}</template><template #content>
              <div class="kv" v-for="r in pmic.rails" :key="r.rail"><span class="k">{{ r.rail }}</span><span class="v">{{ r.voltage_v }} V</span></div></template></Card>
          </div>
        </TabPanel>

        <TabPanel header="Network">
          <DataTable :value="network" size="small">
            <Column field="name" header="Interface"></Column>
            <Column field="method" header="Method"></Column>
            <Column field="address" header="Address"></Column>
            <Column header="Up"><template #body="s"><Tag :severity="s.data.up?'success':'secondary'" :value="String(s.data.up)" /></template></Column>
            <Column header="Actions"><template #body="s">
              <Button v-if="can('admin')" size="small" text :label="s.data.up?'Bring down':'Bring up'" :severity="s.data.up?'warning':'success'" @click="netLink(s.data)" />
            </template></Column>
          </DataTable>
        </TabPanel>

        <TabPanel header="Ports">
          <DataTable :value="ports" size="small">
            <Column field="port" header="Port"></Column>
            <Column field="protocol" header="Proto"></Column>
            <Column field="service" header="Service"></Column>
            <Column field="address" header="Bind"></Column>
            <Column header=""><template #body="s">
              <Button v-if="can('admin')" size="small" text severity="danger" label="Close" @click="closePort(s.data)" /></template></Column>
          </DataTable>
        </TabPanel>

        <TabPanel header="Firewall">
          <Card class="mb-3" v-if="can('admin')"><template #title>Add rule</template><template #content>
            <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:.5rem;align-items:end">
              <span><label>Action</label><Dropdown v-model="newRule.action" :options="['allow','deny']" /></span>
              <span><label>Port</label><InputNumber v-model="newRule.port" :useGrouping="false" data-testid="fw-port" /></span>
              <span><label>Protocol</label><Dropdown v-model="newRule.protocol" :options="['tcp','udp','icmp']" /></span>
              <span><label>Source</label><InputText v-model="newRule.source" /></span>
              <Button label="Add rule" icon="pi pi-plus" data-testid="fw-add" @click="addRule" />
            </div></template></Card>
          <DataTable :value="firewall.rules" size="small">
            <Column field="action" header="Action"><template #body="s"><Tag :severity="s.data.action==='allow'?'success':'danger'" :value="s.data.action" /></template></Column>
            <Column field="port" header="Port"></Column>
            <Column field="protocol" header="Proto"></Column>
            <Column field="source" header="Source"></Column>
            <Column header="Enabled"><template #body="s"><Tag :severity="s.data.enabled?'success':'secondary'" :value="String(s.data.enabled)" /></template></Column>
            <Column header="Actions"><template #body="s">
              <Button v-if="can('admin')" size="small" text :label="s.data.enabled?'Disable':'Enable'" @click="toggleRule(s.data)" />
              <Button v-if="can('admin')" size="small" text severity="danger" icon="pi pi-trash" @click="delRule(s.data)" />
            </template></Column>
          </DataTable>
          <pre v-if="firewall.ruleset" class="mt-2" style="white-space:pre-wrap;background:var(--surface-100);padding:1rem;border-radius:8px">{{ firewall.ruleset }}</pre>
        </TabPanel>

        <TabPanel header="Services">
          <DataTable :value="services" size="small">
            <Column field="name" header="Service"></Column>
            <Column field="active" header="State"><template #body="s"><Tag :severity="s.data.active==='active'?'success':'secondary'" :value="s.data.active" /></template></Column>
            <Column field="enabled" header="Enabled"></Column>
            <Column header="Actions"><template #body="s">
              <Button v-if="can('operator')" size="small" text icon="pi pi-play" @click="svcAction(s.data,'start')" />
              <Button v-if="can('operator')" size="small" text icon="pi pi-refresh" @click="svcAction(s.data,'restart')" />
              <Button v-if="can('operator')" size="small" text severity="warning" icon="pi pi-stop" @click="svcAction(s.data,'stop')" />
            </template></Column>
          </DataTable>
        </TabPanel>

        <TabPanel header="Connectivity">
          <Card class="mb-3"><template #title><i class="pi pi-wifi"></i> WiFi
            <Button class="ml-2" size="small" text icon="pi pi-refresh" label="Scan" @click="scanWifi" /></template>
            <template #content>
              <DataTable :value="wifi" size="small">
                <Column field="ssid" header="SSID"></Column>
                <Column field="signal_dbm" header="Signal (dBm)"></Column>
                <Column field="security" header="Security"></Column>
                <Column header=""><template #body="s">
                  <Button v-if="can('operator')" size="small" text label="Connect" @click="confirmPost('Connect to '+s.data.ssid+'?','/api/sysadmin/wifi/connect',{ssid:s.data.ssid})" /></template></Column>
              </DataTable>
            </template></Card>
          <Card v-if="bt"><template #title><i class="pi pi-bluetooth"></i> Bluetooth</template>
            <template #content>
              <div class="kv" v-for="k in objKeys(bt,['simulated','paired'])" :key="k"><span class="k">{{ k }}</span><span class="v">{{ bt[k] }}</span></div>
              <Button v-if="can('operator')" class="mt-2" size="small" :label="bt.powered?'Disable':'Enable'" :severity="bt.powered?'warning':'success'"
                      @click="confirmPost((bt.powered?'Disable':'Enable')+' Bluetooth?','/api/sysadmin/bluetooth/enable',{enabled:!bt.powered})" />
            </template></Card>
        </TabPanel>
      </TabView>
    </div>
  `,
};

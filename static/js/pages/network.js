import { api } from "../api.js";
import { can } from "../store.js";
import { t } from "../i18n.js";

export default {
    data() {
        return {
            ifaces: null, wifi: null, routing: null, firewall: null, ports: null,
            remote: null, ping: null, pingTarget: "1.1.1.1", timer: null, can
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
            const [ifaces, wifi, routing, firewall, ports, remote, ping] = await Promise.all([
                api.get("/api/network/interfaces"), api.get("/api/network/wifi"),
                api.get("/api/network/routing"), api.get("/api/network/firewall"),
                api.get("/api/network/ports"), api.get("/api/network/remote-access"),
                api.get("/api/network/ping?target=" + encodeURIComponent(this.pingTarget)),
            ]);
            this.ifaces = ifaces; this.wifi = wifi; this.routing = routing;
            this.firewall = firewall; this.ports = ports; this.remote = remote; this.ping = ping;
        },
        fmtRate(b) { return (b / 1024).toFixed(1) + " KB/s"; },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.network') }}</h2>
      <TabView>
        <TabPanel header="Interfaces">
          <DataTable v-if="ifaces" :value="ifaces.interfaces" size="small">
            <Column field="name" header="Name"></Column>
            <Column field="ipv4" header="IPv4"></Column>
            <Column field="mac" header="MAC"></Column>
            <Column header="State"><template #body="s">
              <Tag :severity="s.data.is_up?'success':'secondary'" :value="s.data.is_up?'up':'down'" /></template></Column>
            <Column header="RX"><template #body="s">{{ fmtRate(s.data.rx_rate) }}</template></Column>
            <Column header="TX"><template #body="s">{{ fmtRate(s.data.tx_rate) }}</template></Column>
          </DataTable>
        </TabPanel>
        <TabPanel header="WiFi & Routing">
          <div class="grid-cards">
            <Card><template #title>WiFi</template><template #content>
              <div v-if="wifi">
                <div class="kv"><span class="k">SSID</span><span class="v">{{ wifi.ssid }}</span></div>
                <div class="kv"><span class="k">Signal</span><span class="v">{{ wifi.signal_dbm }} dBm</span></div>
                <div class="kv"><span class="k">Security</span><span class="v">{{ wifi.security }}</span></div>
              </div></template></Card>
            <Card><template #title>Routing / DNS</template><template #content>
              <div v-if="routing">
                <div class="kv"><span class="k">Gateway</span><span class="v">{{ routing.default_gateway }}</span></div>
                <div class="kv"><span class="k">DNS</span><span class="v">{{ routing.dns_servers.join(', ') }}</span></div>
              </div></template></Card>
            <Card><template #title>Latency: {{ pingTarget }}</template><template #content>
              <div v-if="ping" class="kv"><span class="k">Latency</span><span class="v">{{ ping.latency_ms }} ms</span></div>
              <InputText v-model="pingTarget" @keyup.enter="refresh()" class="mt-2" placeholder="ping target" />
            </template></Card>
          </div>
        </TabPanel>
        <TabPanel header="Firewall & Ports">
          <Card><template #title>Firewall ({{ firewall && firewall.backend }})</template><template #content>
            <pre v-if="firewall" style="font-size:.8rem">{{ firewall.ruleset.join('\\n') }}</pre>
          </template></Card>
          <DataTable v-if="ports" :value="ports.ports" size="small" class="mt-3">
            <Column field="proto" header="Proto"></Column>
            <Column field="address" header="Address"></Column>
            <Column field="port" header="Port"></Column>
          </DataTable>
        </TabPanel>
        <TabPanel header="Remote Access">
          <Card v-if="remote"><template #title>SSH & VPN</template><template #content>
            <div class="kv"><span class="k">SSH</span><span class="v">port {{ remote.ssh_port }} ({{ remote.ssh_enabled?'on':'off' }})</span></div>
            <div class="kv"><span class="k">VPN ({{ remote.vpn.type }})</span>
              <Tag :severity="remote.vpn.connected?'success':'secondary'" :value="remote.vpn.connected?'connected':'down'" /></div>
            <div class="kv"><span class="k">Active sessions</span><span class="v">{{ remote.ssh_sessions.length }}</span></div>
            <Button v-if="can('admin')" class="mt-2" label="Open Web Terminal" icon="pi pi-desktop"
                    @click="$router.push('/software')" text />
          </template></Card>
        </TabPanel>
      </TabView>
    </div>
  `,
};

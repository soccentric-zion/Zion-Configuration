import { api } from "../api.js";
import { ReconnectingWS } from "../ws.js";
import { can } from "../store.js";
import { formatTemp, t } from "../i18n.js";

export default {
  data() {
    return {
      identity: null,
      health: null,
      disks: null,
      boot: null,
      cpuHist: [],
      ramHist: [],
      ws: null,
      can,
      // Read the active theme tokens at mount; Chart.js can't read CSS
      // vars at draw time, so resolve once and pass strings.
      chartColors: { cpu: "#007aff", ram: "#5ac8fa" },
    };
  },
  computed: {
    cpuChart() {
      // Clone to plain arrays: Chart.js attaches circular metadata to the
      // data it receives; handing it Vue's reactive proxy triggers an
      // infinite reactivity traversal ("Maximum call stack size exceeded").
      return {
        labels: this.cpuHist.map((_, i) => i),
        datasets: [{
          data: this.cpuHist.slice(),
          borderColor: this.chartColors.cpu,
          backgroundColor: this.chartColors.cpu,
          tension: 0.3,
          pointRadius: 0,
          borderWidth: 1.5,
          fill: false,
        }],
      };
    },
    ramChart() {
      return {
        labels: this.ramHist.map((_, i) => i),
        datasets: [{
          data: this.ramHist.slice(),
          borderColor: this.chartColors.ram,
          backgroundColor: this.chartColors.ram,
          tension: 0.3,
          pointRadius: 0,
          borderWidth: 1.5,
          fill: false,
        }],
      };
    },
    chartOpts() {
      return {
        animation: false,
        plugins: { legend: { display: false } },
        scales: { y: { min: 0, max: 100 }, x: { display: false } },
        maintainAspectRatio: false,
      };
    },
  },
  async mounted() {
    // Resolve theme tokens once; Chart.js cannot read CSS vars at draw
    // time and the values never change during a single SPA session.
    const cs = getComputedStyle(document.documentElement);
    const cpu = cs.getPropertyValue("--accent").trim();
    const ram = cs.getPropertyValue("--info").trim();
    if (cpu) this.chartColors.cpu = cpu;
    if (ram) this.chartColors.ram = ram;

    [this.identity, this.disks, this.boot] = await Promise.all([
      api.get("/api/overview/identity"),
      api.get("/api/overview/disks"),
      api.get("/api/overview/boot"),
    ]);
    this.ws = new ReconnectingWS("/ws/metrics", {
      onMessage: (m) => {
        if (m && m.type === "health") {
          this.health = m.data;
          this.cpuHist.push(Math.round(m.data.cpu_total));
          this.ramHist.push(Math.round(m.data.mem_percent));
          if (this.cpuHist.length > 300) this.cpuHist.shift();
          if (this.ramHist.length > 300) this.ramHist.shift();
        }
      },
    });
    this.ws.connect();
  },
  beforeUnmount() {
    if (this.ws) this.ws.close();
  },
  template: `
    <div>
      <h2 class="page-title">{{ t('nav.home') }}</h2>
      <div class="grid-cards">
        <Card>
          <template #title><i class="pi pi-id-card"></i> Device Identity
            <Tag v-if="identity && identity.simulated" severity="warning" value="sim" /></template>
          <template #content>
            <div v-if="!identity"><Skeleton height="8rem" /></div>
            <div v-else>
              <div class="kv"><span class="k">Model</span><span class="v">{{ identity.model }}</span></div>
              <div class="kv"><span class="k">Serial</span><span class="v">{{ identity.serial_number }}</span></div>
              <div class="kv"><span class="k">Hardware rev</span><span class="v">{{ identity.hardware_revision }}</span></div>
              <div class="kv"><span class="k">Firmware</span><span class="v">{{ identity.firmware_version }}</span></div>
              <div class="kv"><span class="k">OS</span><span class="v">{{ identity.os_name }}</span></div>
              <div class="kv"><span class="k">Kernel</span><span class="v">{{ identity.kernel }}</span></div>
              <div class="kv"><span class="k">Bootloader</span><span class="v">{{ identity.bootloader }}</span></div>
              <div class="kv"><span class="k">MAC</span><span class="v">{{ (identity.mac_addresses||[])[0] }}</span></div>
            </div>
          </template>
        </Card>

        <Card>
          <template #title><i class="pi pi-chart-line"></i> System Health</template>
          <template #content>
            <div v-if="!health"><Skeleton height="8rem" /></div>
            <div v-else>
              <div class="kv"><span class="k">CPU total</span><span class="v">{{ health.cpu_total.toFixed(1) }}%</span></div>
              <div class="kv"><span class="k">CPU temp</span><span class="v">{{ formatTemp(health.cpu_temp_c) }}</span></div>
              <div class="kv"><span class="k">Memory</span><span class="v">{{ health.mem_percent.toFixed(1) }}% ({{ fmtBytes(health.mem_used) }})</span></div>
              <div class="kv"><span class="k">Swap</span><span class="v">{{ health.swap_percent.toFixed(1) }}%</span></div>
              <div class="kv"><span class="k">Load avg</span><span class="v">{{ health.load_avg.map(x=>x.toFixed(2)).join(' ') }}</span></div>
              <div class="kv"><span class="k">Processes</span><span class="v">{{ health.process_count }}</span></div>
              <div class="kv"><span class="k">Uptime</span><span class="v">{{ fmtUptime(health.uptime_s) }}</span></div>
              <div class="kv"><span class="k">Watchdog</span>
                <Tag :severity="health.watchdog_active ? 'success':'secondary'"
                     :value="health.watchdog_active ? 'active':'inactive'" /></div>
            </div>
          </template>
        </Card>

        <Card>
          <template #title>CPU (5 min)</template>
          <template #content><div style="height:160px"><Chart type="line" :data="cpuChart" :options="chartOpts" /></div></template>
        </Card>
        <Card>
          <template #title>Memory (5 min)</template>
          <template #content><div style="height:160px"><Chart type="line" :data="ramChart" :options="chartOpts" /></div></template>
        </Card>

        <Card>
          <template #title><i class="pi pi-database"></i> Disk Usage</template>
          <template #content>
            <DataTable v-if="disks" :value="disks.partitions" size="small" scrollable scrollHeight="200px">
              <Column field="mountpoint" header="Mount"></Column>
              <Column field="fstype" header="FS"></Column>
              <Column header="Used">
                <template #body="s"><ProgressBar :value="Math.round(s.data.percent)" style="height:1rem" /></template>
              </Column>
            </DataTable>
          </template>
        </Card>

        <Card>
          <template #title><i class="pi pi-sync"></i> Boot Management
            <Tag v-if="boot && boot.simulated" severity="warning" value="sim" /></template>
          <template #content>
            <div v-if="boot">
              <div class="kv"><span class="k">Active slot</span><span class="v">{{ boot.active_slot }}</span></div>
              <div class="kv"><span class="k">Fallback</span><span class="v">{{ boot.fallback_slot }}</span></div>
              <div class="kv"><span class="k">Boot count</span><span class="v">{{ boot.boot_count }}</span></div>
              <div class="kv"><span class="k">Boot reason</span><span class="v">{{ boot.boot_reason }}</span></div>
              <div class="kv"><span class="k">Boot time</span><span class="v">{{ boot.boot_total_s }}s</span></div>
              <Button v-if="can('admin')" class="mt-2" severity="danger" outlined size="small"
                      icon="pi pi-undo" label="Rollback" @click="rollback" />
            </div>
          </template>
        </Card>
      </div>
    </div>
  `,
  methods: {
    t, formatTemp,
    fmtBytes(b) {
      if (!b) return "0";
      const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; let v = b;
      while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
      return v.toFixed(1) + " " + u[i];
    },
    fmtUptime(s) {
      const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
      return `${d}d ${h}h ${m}m`;
    },
    rollback() {
      this.$confirm.require({
        message: "Trigger A/B rollback? The device will reboot into the fallback slot.",
        header: "Confirm rollback", icon: "pi pi-exclamation-triangle",
        accept: async () => {
          await api.post("/api/overview/boot/rollback");
          this.$toast.add({ severity: "success", summary: "Rollback armed", life: 3000 });
        },
      });
    },
  },
};

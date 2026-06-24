import { api } from "../api.js";
import { can } from "../store.js";
import { formatTemp, t } from "../i18n.js";

export default {
    data() {
        return {
            zones: null, fans: null, throttle: null, watchdog: null,
            tripPoints: null, coolingPolicy: null, history: null,
            timer: null, can, newPolicy: "active",
        };
    },
    async mounted() {
        await this.refresh();
        this.timer = setInterval(() => this.refresh(), 3000);
    },
    beforeUnmount() { clearInterval(this.timer); },
    methods: {
        t, formatTemp,
        async refresh() {
            [this.zones, this.fans, this.throttle, this.watchdog,
             this.tripPoints, this.coolingPolicy, this.history] = await Promise.all([
                api.get("/api/thermal/zones"), api.get("/api/thermal/fans"),
                api.get("/api/thermal/throttling"), api.get("/api/thermal/watchdog"),
                api.get("/api/thermal/trip-points"), api.get("/api/thermal/cooling-policy"),
                api.get("/api/thermal/history?limit=60"),
            ]);
        },
        async applyPolicy() {
            this.$confirm.require({
                message: "Change cooling policy to " + this.newPolicy + "?",
                header: "Confirm", icon: "pi pi-cog",
                accept: async () => {
                    await api.put("/api/thermal/cooling-policy", { policy: this.newPolicy, confirm: true });
                    this.$toast.add({ severity: "success", summary: "Cooling policy: " + this.newPolicy });
                    await this.refresh();
                },
            });
        },
        testWatchdog() {
            this.$confirm.require({
                message: "This will FIRE the watchdog and REBOOT the device. Are you absolutely sure?",
                header: "Watchdog test (triple confirm)", icon: "pi pi-exclamation-triangle",
                acceptClass: "p-button-danger",
                accept: () => this.$confirm.require({
                    message: "Second confirmation: the device WILL reboot. Continue?",
                    header: "Confirm again", icon: "pi pi-exclamation-triangle", acceptClass: "p-button-danger",
                    accept: () => this.$confirm.require({
                        message: "Final confirmation. Fire watchdog now?",
                        header: "Final confirm", icon: "pi pi-exclamation-triangle", acceptClass: "p-button-danger",
                        accept: async () => {
                            await api.post("/api/thermal/watchdog/test");
                            this.$toast.add({ severity: "warn", summary: "Watchdog armed (simulated)", life: 3000 });
                        },
                    }),
                }),
            });
        },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.thermal') }}</h2>
      <div class="grid-cards">
        <Card><template #title>Thermal Zones</template><template #content>
          <DataTable v-if="zones" :value="zones.zones" size="small">
            <Column field="name" header="Zone"></Column>
            <Column header="Temp"><template #body="s">{{ formatTemp(s.data.temp_c) }}</template></Column>
            <Column header="Trip high"><template #body="s">{{ s.data.trip_high_c }}°C</template></Column>
            <Column header="Trip crit"><template #body="s">{{ s.data.trip_crit_c }}°C</template></Column>
          </DataTable>
        </template></Card>
        <Card><template #title>Trip Points</template><template #content>
          <DataTable v-if="tripPoints" :value="tripPoints.zones" size="small">
            <Column field="name" header="Zone"></Column>
            <Column field="temp_c" header="Now"></Column>
            <Column field="trip_high_c" header="High"></Column>
            <Column field="trip_crit_c" header="Crit"></Column>
          </DataTable>
        </template></Card>
        <Card><template #title>Fans</template><template #content>
          <DataTable v-if="fans" :value="fans.fans" size="small">
            <Column field="name" header="Fan"></Column>
            <Column field="rpm" header="RPM"></Column>
            <Column field="mode" header="Mode"></Column>
          </DataTable>
        </template></Card>
        <Card><template #title>Throttling</template><template #content>
          <div v-if="throttle">
            <div class="kv"><span class="k">CPU freq</span><span class="v">{{ throttle.cpu_freq_mhz }} / {{ throttle.cpu_freq_max_mhz }} MHz</span></div>
            <div class="kv"><span class="k">Throttled</span><Tag :severity="throttle.throttled?'danger':'success'" :value="String(throttle.throttled)" /></div>
            <div class="kv"><span class="k">Policy</span><span class="v">{{ throttle.cooling_policy }}</span></div>
          </div>
        </template></Card>
        <Card><template #title>Cooling Policy</template><template #content>
          <div v-if="coolingPolicy">
            <div class="kv"><span class="k">Active</span>
              <Tag :value="coolingPolicy.policy" /></div>
            <div class="kv" v-if="can('admin')"><span class="k">Set</span>
              <SelectButton :options="coolingPolicy.available" v-model="newPolicy" /></div>
            <Button v-if="can('admin')" class="mt-2" icon="pi pi-save"
                    label="Apply" :disabled="newPolicy === coolingPolicy.policy"
                    @click="applyPolicy" />
          </div>
        </template></Card>
        <Card><template #title>CPU Temp History</template><template #content>
          <div v-if="history && history.count">
            <Chart type="line" :data="{
              labels: history.points.map(p => p.ts.slice(11, 19)),
              datasets: [{ label: 'CPU °C', data: history.points.map(p => p.temp_c), fill: false, tension: 0.3 }],
            }" :options="{ responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } }" style="height: 180px;" />
            <div class="kv mt-1"><span class="k">Samples</span><span class="v">{{ history.count }}</span></div>
          </div>
          <div v-else><span class="v muted">No samples yet — sampler writes every {{ interval || 10 }}s.</span></div>
        </template></Card>
        <Card><template #title>Watchdog</template><template #content>
          <div v-if="watchdog">
            <div class="kv"><span class="k">Type</span><span class="v">{{ watchdog.type }}</span></div>
            <div class="kv"><span class="k">Timeout</span><span class="v">{{ watchdog.timeout_s }}s</span></div>
            <Button v-if="can('admin')" class="mt-2" severity="danger" outlined size="small"
                    icon="pi pi-bolt" label="Test-fire watchdog" @click="testWatchdog" />
          </div>
        </template></Card>
      </div>
    </div>
  `,
};

import { api } from "../api.js";
import { t } from "../i18n.js";

export default {
    data() { return { health: null, mounts: null, power: null, rtc: null }; },
    async mounted() {
        [this.health, this.mounts, this.power, this.rtc] = await Promise.all([
            api.get("/api/storage/health"), api.get("/api/storage/mounts"),
            api.get("/api/storage/power"), api.get("/api/storage/rtc"),
        ]);
    },
    methods: { t },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.storage') }}</h2>
      <div class="grid-cards">
        <Card><template #title>eMMC / Storage Health</template><template #content>
          <div v-if="health">
            <div class="kv"><span class="k">eMMC life A</span><span class="v">{{ health.emmc.life_time_a_pct }}%</span></div>
            <div class="kv"><span class="k">Pre-EOL</span><span class="v">{{ health.emmc.pre_eol }}</span></div>
            <div class="kv"><span class="k">Size</span><span class="v">{{ health.emmc.size_gb }} GB</span></div>
            <Divider />
            <DataTable :value="health.smart" size="small">
              <Column field="device" header="Device"></Column>
              <Column field="health" header="Health"></Column>
              <Column header="Temp"><template #body="s">{{ s.data.temperature_c }}°C</template></Column>
            </DataTable>
          </div>
        </template></Card>
        <Card><template #title>Mounts</template><template #content>
          <DataTable v-if="mounts" :value="mounts.mounts" size="small" scrollable scrollHeight="200px">
            <Column field="mountpoint" header="Mount"></Column>
            <Column header="Used"><template #body="s"><ProgressBar :value="Math.round(s.data.percent)" style="height:1rem" /></template></Column>
          </DataTable>
        </template></Card>
        <Card><template #title>Power</template><template #content>
          <div v-if="power">
            <div class="kv"><span class="k">Source</span><span class="v">{{ power.source }}</span></div>
            <div class="kv"><span class="k">Input V</span><span class="v">{{ power.input_voltage_v }} V</span></div>
            <div class="kv"><span class="k">Input A</span><span class="v">{{ power.input_current_a }} A</span></div>
            <div class="kv"><span class="k">Wake sources</span><span class="v">{{ power.wake_sources.join(', ') }}</span></div>
          </div>
        </template></Card>
        <Card><template #title>RTC</template><template #content>
          <div v-if="rtc">
            <div class="kv"><span class="k">RTC time</span><span class="v">{{ rtc.rtc_time }}</span></div>
            <div class="kv"><span class="k">Drift</span><span class="v">{{ rtc.drift_ppm }} ppm</span></div>
            <div class="kv"><span class="k">NTP synced</span><Tag :severity="rtc.ntp_synced?'success':'warning'" :value="String(rtc.ntp_synced)" /></div>
          </div>
        </template></Card>
      </div>
    </div>
  `,
};

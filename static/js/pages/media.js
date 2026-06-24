import { api } from "../api.js";
import { can } from "../store.js";
import { t } from "../i18n.js";
import { ReconnectingWS } from "../ws.js";

export default {
    data() {
        return {
            cameras: [], displays: [], blocks: [], isp: null, hvs: null, jobs: [],
            simulated: false, can, frame: null, streaming: null, ws: null, timer: null,
        };
    },
    async mounted() {
        await this.refresh();
        this.timer = setInterval(() => this.refresh(true), 4000);
    },
    beforeUnmount() { clearInterval(this.timer); if (this.ws) this.ws.close(); },
    methods: {
        t,
        async refresh(quiet) {
            const [cams, disp, blk, isp, hvs, jobs] = await Promise.all([
                api.get("/api/media/cameras"), api.get("/api/media/displays"), api.get("/api/media/blocks"),
                api.get("/api/media/isp"), api.get("/api/media/hvs"), api.get("/api/media/jobs"),
            ]);
            this.cameras = cams.cameras; this.simulated = cams.simulated;
            this.displays = disp.displays; this.blocks = blk.blocks;
            this.isp = isp; this.hvs = hvs; this.jobs = jobs.jobs;
        },
        async toggle(item) {
            const r = await api.post(`/api/media/${item.name}/enable`, { enabled: !item.enabled });
            item.enabled = r.enabled;
            this.$toast.add({ severity: "success", summary: `${item.name} ${r.enabled ? "on" : "off"}`, life: 1200 });
        },
        stream(cam) {
            if (this.streaming === cam.name) { this.stop(); return; }
            this.stop();
            this.streaming = cam.name;
            this.ws = new ReconnectingWS(`/ws/camera?name=${cam.name}`, {
                onMessage: (m) => { if (m && m.data) this.frame = m; },
            });
            this.ws.connect();
        },
        stop() {
            if (this.ws) { this.ws.close(); this.ws = null; }
            this.streaming = null; this.frame = null;
        },
        async startJob(kind) {
            await api.post(`/api/media/jobs/${kind}`, { source: "csi0", sink: "file" });
            await this.refresh();
        },
        async stopJob(j) { await api.del(`/api/media/jobs/${j.job_id}`); await this.refresh(); },
        blockKeys(b) { return Object.keys(b).filter((k) => !["name", "kind", "enabled", "simulated", "config", "status"].includes(k)); },
        statusKeys(s) { return s ? Object.keys(s).filter((k) => k !== "simulated") : []; },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.media') }}
        <Tag v-if="simulated" severity="warning" :value="t('common.simulated')" icon="pi pi-desktop" class="ml-2" />
      </h2>
      <TabView>
        <TabPanel header="Cameras">
          <div class="grid-cards">
            <Card v-for="c in cameras" :key="c.name">
              <template #title>
                <i class="pi pi-camera"></i> {{ c.name }}
                <Tag class="ml-2" :severity="c.enabled?'success':'secondary'" :value="c.enabled?'enabled':'disabled'" />
              </template>
              <template #content>
                <div class="kv"><span class="k">resolution</span><span class="v">{{ c.config.width }}×{{ c.config.height }}</span></div>
                <div class="kv"><span class="k">format</span><span class="v">{{ c.config.format }}</span></div>
                <div v-if="c.status" class="kv"><span class="k">sensor</span><span class="v">{{ c.status.sensor }}</span></div>
                <div v-if="streaming===c.name && frame" class="mt-2">
                  <img :src="'data:'+frame.mime+';base64,'+frame.data" style="width:100%;border-radius:8px;border:1px solid var(--surface-border)" />
                  <div class="text-muted">frame #{{ frame.seq }}</div>
                </div>
                <div class="mt-2">
                  <Button v-if="can('operator')" size="small" outlined class="mr-2"
                          :label="c.enabled?'Disable':'Enable'" :severity="c.enabled?'warning':'success'" @click="toggle(c)" />
                  <Button size="small" :label="streaming===c.name?'Stop':'Live view'"
                          :icon="streaming===c.name?'pi pi-stop':'pi pi-play'"
                          :data-testid="'cam-stream-'+c.name" @click="stream(c)" />
                </div>
              </template>
            </Card>
          </div>
        </TabPanel>

        <TabPanel header="Displays">
          <div class="grid-cards">
            <Card v-for="d in displays" :key="d.name">
              <template #title><i class="pi pi-desktop"></i> {{ d.name }}
                <Tag class="ml-2" :severity="d.enabled?'success':'secondary'" :value="d.enabled?'enabled':'disabled'" /></template>
              <template #content>
                <div class="kv" v-for="k in blockKeys(d)" :key="k"><span class="k">{{ k }}</span><span class="v">{{ d[k] }}</span></div>
                <Button v-if="can('operator')" size="small" outlined class="mt-2"
                        :label="d.enabled?'Disable':'Enable'" :severity="d.enabled?'warning':'success'" @click="toggle(d)" />
              </template>
            </Card>
          </div>
        </TabPanel>

        <TabPanel header="Codec & SoC blocks">
          <div class="grid-cards">
            <Card v-for="b in blocks" :key="b.name">
              <template #title><i class="pi pi-microchip"></i> {{ b.name }}
                <Tag class="ml-2" :value="b.kind" severity="info" /></template>
              <template #content>
                <div class="kv" v-for="k in statusKeys(b.status)" :key="k"><span class="k">{{ k }}</span><span class="v">{{ b.status[k] }}</span></div>
              </template>
            </Card>
            <Card v-if="isp"><template #title><i class="pi pi-image"></i> ISP</template>
              <template #content><div class="kv" v-for="k in statusKeys(isp.status)" :key="k"><span class="k">{{ k }}</span><span class="v">{{ isp.status[k] }}</span></div></template></Card>
            <Card v-if="hvs"><template #title><i class="pi pi-clone"></i> HVS scaler</template>
              <template #content><div class="kv" v-for="k in statusKeys(hvs.status)" :key="k"><span class="k">{{ k }}</span><span class="v">{{ hvs.status[k] }}</span></div></template></Card>
          </div>
          <Card class="mt-3"><template #title>Transcode jobs</template>
            <template #content>
              <div class="mb-2" v-if="can('operator')">
                <Button size="small" label="Decode (HEVC HW)" icon="pi pi-play" class="mr-2" data-testid="job-decode" @click="startJob('decode')" />
                <Button size="small" label="Encode (SW H.264)" icon="pi pi-play" outlined @click="startJob('encode')" />
              </div>
              <DataTable :value="jobs" size="small">
                <Column field="job_id" header="Job"></Column>
                <Column field="kind" header="Kind"></Column>
                <Column field="status" header="State"></Column>
                <Column header=""><template #body="s">
                  <Button v-if="can('operator')" size="small" text severity="danger" icon="pi pi-stop" @click="stopJob(s.data)" /></template></Column>
              </DataTable>
            </template>
          </Card>
        </TabPanel>
      </TabView>
    </div>
  `,
};

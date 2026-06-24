import { api } from "../api.js";
import { ReconnectingWS } from "../ws.js";
import { can } from "../store.js";
import { t } from "../i18n.js";

export default {
    data() {
        return {
            gpio: null, pwm: null, adc: null, sensors: null, usb: null, i2c: null,
            can_status: null, mqtt: null, modbus: null, canFrames: [], canWs: null,
            timer: null, canEnabled: false, can
        };
    },
    async mounted() {
        await this.loadAll();
        this.timer = setInterval(() => this.loadLive(), 2000);
    },
    beforeUnmount() { clearInterval(this.timer); if (this.canWs) this.canWs.close(); },
    methods: {
        t,
        async loadAll() {
            const [gpio, pwm, adc, sensors, usb, i2c, cs, mqtt, modbus] = await Promise.all([
                api.get("/api/hardware/gpio"), api.get("/api/hardware/pwm"), api.get("/api/hardware/adc"),
                api.get("/api/hardware/sensors"), api.get("/api/hardware/usb"), api.get("/api/hardware/i2c?bus=1"),
                api.get("/api/hardware/can"), api.get("/api/hardware/mqtt"),
                api.get("/api/hardware/modbus?unit=1&function=3&address=0&count=8"),
            ]);
            this.gpio = gpio; this.pwm = pwm; this.adc = adc; this.sensors = sensors;
            this.usb = usb; this.i2c = i2c; this.can_status = cs; this.mqtt = mqtt; this.modbus = modbus;
        },
        async loadLive() {
            this.gpio = await api.get("/api/hardware/gpio");
            this.adc = await api.get("/api/hardware/adc");
            this.sensors = await api.get("/api/hardware/sensors");
        },
        async toggleGpio(pin, current) {
            const next = current ? 0 : 1;
            const r = await api.post("/api/hardware/gpio/" + pin, { value: next });
            const p = this.gpio.pins.find((x) => x.pin === pin);
            if (p) p.value = r.value;
            this.$toast.add({ severity: "success", summary: "GPIO " + pin + " = " + r.value, life: 1500 });
        },
        toggleCan() {
            this.canEnabled = !this.canEnabled;
            if (this.canEnabled) {
                this.canWs = new ReconnectingWS("/ws/can", {
                    onMessage: (f) => {
                        this.canFrames.unshift(f); if (this.canFrames.length > 50) this.canFrames.pop();
                    }
                });
                this.canWs.connect();
            } else if (this.canWs) { this.canWs.close(); this.canWs = null; }
        },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.hardware') }}</h2>
      <TabView>
        <TabPanel header="GPIO / PWM">
          <DataTable v-if="gpio" :value="gpio.pins" size="small" data-testid="gpio-table">
            <Column field="pin" header="Pin"></Column>
            <Column field="direction" header="Dir"></Column>
            <Column header="Value"><template #body="s">
              <Tag :severity="s.data.value?'success':'secondary'" :value="String(s.data.value)" :data-testid="'gpio-val-'+s.data.pin" /></template></Column>
            <Column header="Toggle"><template #body="s">
              <Button v-if="can('operator') && s.data.direction==='out'" size="small" outlined
                      :data-testid="'gpio-toggle-'+s.data.pin" label="Toggle" @click="toggleGpio(s.data.pin, s.data.value)" />
            </template></Column>
          </DataTable>
        </TabPanel>
        <TabPanel header="ADC / Sensors">
          <div class="grid-cards">
            <Card><template #title>ADC channels</template><template #content>
              <DataTable v-if="adc" :value="adc.channels" size="small">
                <Column field="channel" header="Ch"></Column>
                <Column field="raw" header="Raw"></Column>
                <Column header="Voltage"><template #body="s">{{ s.data.voltage }} V</template></Column>
              </DataTable></template></Card>
            <Card><template #title>Sensors</template><template #content>
              <DataTable v-if="sensors" :value="sensors.sensors" size="small">
                <Column field="name" header="Name"></Column>
                <Column field="type" header="Type"></Column>
                <Column header="Value"><template #body="s">{{ s.data.value }} {{ s.data.unit }}</template></Column>
              </DataTable></template></Card>
          </div>
        </TabPanel>
        <TabPanel header="Buses">
          <div class="grid-cards">
            <Card><template #title>USB devices</template><template #content>
              <ul><li v-for="u in (usb && usb.tree)" :key="u.device">{{ u.name }}</li></ul></template></Card>
            <Card><template #title>I2C bus 1</template><template #content>
              <div v-if="i2c">Detected: <Tag v-for="a in i2c.devices" :key="a" :value="a" class="mr-1" /></div></template></Card>
            <Card><template #title>MQTT</template><template #content>
              <div v-if="mqtt"><div class="kv"><span class="k">Broker</span><span class="v">{{ mqtt.broker }}</span></div>
              <div class="kv"><span class="k">Connected</span><Tag :severity="mqtt.connected?'success':'danger'" :value="String(mqtt.connected)" /></div></div></template></Card>
            <Card><template #title>Modbus registers</template><template #content>
              <DataTable v-if="modbus" :value="modbus.registers" size="small" scrollable scrollHeight="160px">
                <Column field="address" header="Addr"></Column><Column field="value" header="Value"></Column>
              </DataTable></template></Card>
          </div>
        </TabPanel>
        <TabPanel header="CAN monitor">
          <Button :label="canEnabled?'Pause':'Resume'" :icon="canEnabled?'pi pi-pause':'pi pi-play'" @click="toggleCan" class="mb-2" />
          <DataTable :value="canFrames" size="small" scrollable scrollHeight="300px">
            <Column field="ts" header="Time"></Column><Column field="id" header="ID"></Column>
            <Column field="dlc" header="DLC"></Column><Column field="data" header="Data"></Column>
          </DataTable>
        </TabPanel>
      </TabView>
    </div>
  `,
};

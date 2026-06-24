// Password policy, 2FA, session timeout.
import { api } from "../api.js";
import { can } from "../store.js";
import { t } from "../i18n.js";

export default {
    data() {
        return {
            can, pwd: null, timeout: 30, tfa: false,
            form: {
                min_length: 12, require_upper: true, require_lower: true,
                require_digit: true, require_symbol: true,
                expiry_days: 0, history_count: 5,
            },
        };
    },
    async mounted() {
        this.pwd = await api.get("/api/policy/password");
        if (this.pwd) {
            Object.assign(this.form, {
                min_length: this.pwd.min_length, require_upper: !!this.pwd.require_upper,
                require_lower: !!this.pwd.require_lower, require_digit: !!this.pwd.require_digit,
                require_symbol: !!this.pwd.require_symbol, expiry_days: this.pwd.expiry_days,
                history_count: this.pwd.history_count,
            });
        }
    },
    methods: {
        t,
        async savePwd() {
            await api.put("/api/policy/password", this.form);
            this.$toast.add({ severity: "success", summary: "Password policy updated" });
        },
        async saveTimeout() {
            await api.put("/api/policy/session-timeout", { idle_minutes: this.timeout });
            this.$toast.add({ severity: "success", summary: "Session timeout: " + this.timeout + "m" });
        },
        async save2fa() {
            await api.put("/api/policy/2fa", { enabled: this.tfa });
            this.$toast.add({ severity: "success", summary: "2FA " + (this.tfa ? "required" : "optional") });
        },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.policy') }}</h2>
      <div class="grid-cards" v-if="can('admin')">
        <Card><template #title><i class="pi pi-key"></i> Password policy</template>
          <template #content>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:.5rem;align-items:center">
              <label>Min length</label><InputNumber v-model="form.min_length" :min="6" :max="128" />
              <label>Require upper</label><InputSwitch v-model="form.require_upper" />
              <label>Require lower</label><InputSwitch v-model="form.require_lower" />
              <label>Require digit</label><InputSwitch v-model="form.require_digit" />
              <label>Require symbol</label><InputSwitch v-model="form.require_symbol" />
              <label>Expiry (days, 0=never)</label><InputNumber v-model="form.expiry_days" :min="0" :max="3650" />
              <label>History count</label><InputNumber v-model="form.history_count" :min="0" :max="50" />
            </div>
            <Button class="mt-3" icon="pi pi-save" label="Apply" @click="savePwd" />
          </template>
        </Card>
        <Card><template #title><i class="pi pi-clock"></i> Session timeout</template>
          <template #content>
            <p class="text-muted">Idle minutes before the dashboard logs the user out.</p>
            <InputNumber v-model="timeout" :min="1" :max="1440" class="mb-2" />
            <Button icon="pi pi-save" label="Apply" @click="saveTimeout" />
          </template>
        </Card>
        <Card><template #title><i class="pi pi-mobile"></i> Two-factor auth</template>
          <template #content>
            <p class="text-muted">Require TOTP for all admin logins.</p>
            <InputSwitch v-model="tfa" />
            <Button class="ml-2" icon="pi pi-save" label="Apply" @click="save2fa" />
          </template>
        </Card>
      </div>
    </div>
  `,
};

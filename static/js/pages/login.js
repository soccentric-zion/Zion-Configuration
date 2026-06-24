import { api, setToken } from "../api.js";
import { loadMe, loadSummary, store } from "../store.js";
import { t } from "../i18n.js";

export default {
    data() {
        return { username: "admin", password: "admin", loading: false, error: "" };
    },
    methods: {
        t,
        async submit() {
            this.loading = true;
            this.error = "";
            try {
                const r = await api.post("/api/login", { username: this.username, password: this.password });
                setToken(r.access_token);
                await loadMe();
                await loadSummary();
                if (r.must_change_password) {
                    this.$toast.add({
                        severity: "warn", summary: "Password change required",
                        detail: "Please change the default password in Security.", life: 6000
                    });
                }
                this.$router.push("/home");
            } catch (e) {
                this.error = e.message || "Login failed";
            } finally {
                this.loading = false;
            }
        },
    },
    template: `
    <div class="login-wrap">
      <Card class="login-card">
        <template #title>{{ t('login.title') }}</template>
        <template #content>
          <div class="flex flex-column gap-3">
            <span class="p-float-label" style="margin-top:.5rem">
              <InputText id="u" v-model="username" style="width:100%" @keyup.enter="submit" />
              <label for="u">{{ t('login.username') }}</label>
            </span>
            <span class="p-float-label" style="margin-top:.75rem">
              <Password id="p" v-model="password" :feedback="false" toggleMask inputStyle="width:100%"
                        style="width:100%" @keyup.enter="submit" />
              <label for="p">{{ t('login.password') }}</label>
            </span>
            <Message v-if="error" severity="error" :closable="false">{{ error }}</Message>
            <Button :label="t('login.submit')" :loading="loading" @click="submit" />
            <small class="text-color-secondary">Default: admin / admin</small>
          </div>
        </template>
      </Card>
    </div>
  `,
};

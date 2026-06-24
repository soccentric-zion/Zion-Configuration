// Dashboard about: version, platform, build, open-source attribution.
import { api } from "../api.js";
import { t } from "../i18n.js";

export default {
    data() {
        return { about: null };
    },
    async mounted() { this.about = await api.get("/api/about"); },
    methods: { t },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.about') }}</h2>
      <div v-if="!about"><Skeleton height="6rem" /></div>
      <div v-else class="grid-cards">
        <Card><template #title><i class="pi pi-info-circle"></i> Dashboard</template>
          <template #content>
            <div class="kv"><span class="k">Version</span><span class="v">{{ about.version }}</span></div>
            <div class="kv"><span class="k">Build</span><span class="v">{{ about.build_date }}</span></div>
            <div class="kv"><span class="k">Python</span><span class="v">{{ about.python }}</span></div>
            <div class="kv"><span class="k">License</span><span class="v">{{ about.license }}</span></div>
            <div v-if="about.simulated" class="mt-2"><Tag severity="warning" :value="t('common.simulated')" /></div>
          </template>
        </Card>
        <Card><template #title><i class="pi pi-server"></i> Platform</template>
          <template #content>
            <div class="kv"><span class="k">Name</span><span class="v">{{ about.platform.name }}</span></div>
            <div class="kv"><span class="k">Codename</span><span class="v">{{ about.platform.codename }}</span></div>
            <div class="kv"><span class="k">Description</span><span class="v">{{ about.platform.description }}</span></div>
          </template>
        </Card>
        <Card v-if="about.open_source_attributions && about.open_source_attributions.length">
          <template #title><i class="pi pi-book"></i> Open Source</template>
          <template #content>
            <ul style="margin:0;padding-left:1.25rem">
              <li v-for="(a, i) in about.open_source_attributions" :key="i">{{ a }}</li>
            </ul>
          </template>
        </Card>
      </div>
    </div>
  `,
};

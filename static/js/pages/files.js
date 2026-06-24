import { api, getToken } from "../api.js";
import { can } from "../store.js";
import { t } from "../i18n.js";

export default {
    data() {
        return {
            roots: [], cwd: "", listing: null, editor: { open: false, path: "", content: "", editable: true },
            logs: null, diag: null, can
        };
    },
    async mounted() {
        const r = await api.get("/api/files/roots");
        this.roots = r.roots;
        this.cwd = this.roots[0];
        await this.browse(this.cwd);
    },
    computed: {
        crumbs() { return this.cwd ? this.cwd.split("/").filter(Boolean) : []; },
    },
    methods: {
        t,
        async browse(path) {
            try {
                this.listing = await api.get("/api/files/list?path=" + encodeURIComponent(path));
                this.cwd = this.listing.path;
            } catch (e) { this.$toast.add({ severity: "error", summary: e.message, life: 3000 }); }
        },
        enter(entry) {
            if (entry.is_dir) this.browse(this.cwd.replace(/\/$/, "") + "/" + entry.name);
            else this.openFile(entry);
        },
        crumbTo(i) { this.browse("/" + this.crumbs.slice(0, i + 1).join("/")); },
        async openFile(entry) {
            const p = this.cwd.replace(/\/$/, "") + "/" + entry.name;
            const r = await api.get("/api/files/read?path=" + encodeURIComponent(p));
            this.editor = { open: true, path: r.path, content: r.content, editable: r.editable };
        },
        async save() {
            await api.post("/api/files/write", { path: this.editor.path, content: this.editor.content });
            this.$toast.add({ severity: "success", summary: "Saved", life: 1500 });
            this.editor.open = false;
        },
        download(entry) {
            const p = this.cwd.replace(/\/$/, "") + "/" + entry.name;
            window.open("/api/files/download?path=" + encodeURIComponent(p) + "&_t=" + getToken(), "_blank");
        },
        confirmDelete(entry) {
            this.$confirm.require({
                message: "Delete " + entry.name + "?", header: "Confirm delete", icon: "pi pi-trash",
                acceptClass: "p-button-danger",
                accept: async () => {
                    const p = this.cwd.replace(/\/$/, "") + "/" + entry.name;
                    await api.del("/api/files/delete?path=" + encodeURIComponent(p));
                    this.browse(this.cwd);
                },
            });
        },
        async loadLogs() { this.logs = await api.get("/api/files/logs/journal?lines=80"); },
        async runDiag() {
            this.diag = null;
            this.diag = await api.post("/api/files/diagnostics/run");
        },
    },
    template: `
    <div>
      <h2 class="page-title">{{ t('nav.files') }}</h2>
      <TabView @tab-change="(e)=>{ if(e.index===1) loadLogs(); }">
        <TabPanel header="File Manager">
          <div class="mb-2">
            <a href="#" @click.prevent="browse('/'+crumbs.slice(0,0).join('/'))">/</a>
            <span v-for="(c,i) in crumbs" :key="i">
              <a href="#" @click.prevent="crumbTo(i)">{{ c }}</a> /
            </span>
          </div>
          <DataTable v-if="listing" :value="listing.entries" size="small" scrollable scrollHeight="380px">
            <Column header="Name"><template #body="s">
              <a href="#" @click.prevent="enter(s.data)"><i :class="s.data.is_dir?'pi pi-folder':'pi pi-file'"></i> {{ s.data.name }}</a>
            </template></Column>
            <Column field="size" header="Size"></Column>
            <Column field="mode" header="Perms"></Column>
            <Column field="mtime" header="Modified"></Column>
            <Column header=""><template #body="s">
              <Button v-if="!s.data.is_dir" text size="small" icon="pi pi-download" @click="download(s.data)" />
              <Button v-if="!s.data.is_dir && can('admin')" text size="small" icon="pi pi-trash" @click="confirmDelete(s.data)" />
            </template></Column>
          </DataTable>
          <Dialog v-model:visible="editor.open" :header="editor.path" modal style="width:60vw">
            <Textarea v-model="editor.content" :readonly="!editable || !can('admin')" rows="18" style="width:100%" />
            <template #footer>
              <Button text :label="t('common.cancel')" @click="editor.open=false" />
              <Button v-if="can('admin') && editor.editable" :label="t('common.save')" @click="save" />
            </template>
          </Dialog>
        </TabPanel>
        <TabPanel header="Logs">
          <Button label="Refresh" icon="pi pi-refresh" @click="loadLogs" class="mb-2" />
          <pre v-if="logs" style="max-height:420px;overflow:auto;font-size:.8rem">{{ logs.lines.join('\\n') }}</pre>
        </TabPanel>
        <TabPanel header="Diagnostics">
          <Button label="Run self-test" icon="pi pi-play" @click="runDiag" class="mb-2" />
          <Button label="Export bundle" icon="pi pi-download" text
                  @click="window.open('/api/files/diagnostics/bundle')" />
          <DataTable v-if="diag" :value="diag.results" size="small" class="mt-2">
            <Column field="name" header="Test"></Column>
            <Column header="Result"><template #body="s">
              <Tag :severity="s.data.passed?'success':'danger'" :value="s.data.passed?'PASS':'FAIL'" /></template></Column>
            <Column header="Duration"><template #body="s">{{ s.data.duration_s }}s</template></Column>
            <Column field="detail" header="Detail"></Column>
          </DataTable>
        </TabPanel>
      </TabView>
    </div>
  `,
};

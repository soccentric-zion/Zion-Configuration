// Registers PrimeVue plugin, services, directives and components on the app.
// PrimeVue 3 UMD exposes everything under window.primevue.<name>.

function g(name) {
    const m = window.primevue[name];
    if (!m) {
        console.warn("PrimeVue module missing:", name);
        return null;
    }
    return m.default || m;
}

const COMPONENTS = {
    Button: "button",
    InputText: "inputtext",
    Password: "password",
    Textarea: "textarea",
    InputNumber: "inputnumber",
    InputSwitch: "inputswitch",
    Dropdown: "dropdown",
    SelectButton: "selectbutton",
    Slider: "slider",
    Knob: "knob",
    Card: "card",
    Panel: "panel",
    Toolbar: "toolbar",
    Tag: "tag",
    Badge: "badge",
    Message: "message",
    Divider: "divider",
    DataTable: "datatable",
    Column: "column",
    TabView: "tabview",
    TabPanel: "tabpanel",
    PanelMenu: "panelmenu",
    Menu: "menu",
    Breadcrumb: "breadcrumb",
    Accordion: "accordion",
    AccordionTab: "accordiontab",
    Dialog: "dialog",
    ConfirmDialog: "confirmdialog",
    Toast: "toast",
    ProgressBar: "progressbar",
    FileUpload: "fileupload",
    Skeleton: "skeleton",
    ScrollPanel: "scrollpanel",
    Avatar: "avatar",
    Chart: "chart",
};

export function registerPrimeVue(app) {
    app.use(g("config"));
    app.use(g("toastservice"));
    app.use(g("confirmationservice"));

    const ripple = g("ripple");
    const tooltip = g("tooltip");
    if (ripple) app.directive("ripple", ripple);
    if (tooltip) app.directive("tooltip", tooltip);

    for (const [tag, name] of Object.entries(COMPONENTS)) {
        const comp = g(name);
        if (comp) app.component(tag, comp);
    }
}

// Re-export the Vue / Vue Router globals (loaded as UMD in index.html) so the
// rest of the app can use clean ES-module imports without a build step.
export const Vue = window.Vue;
export const {
    createApp,
    reactive,
    ref,
    computed,
    watch,
    onMounted,
    onBeforeUnmount,
    nextTick,
    defineComponent,
    h,
} = window.Vue;

export const VueRouter = window.VueRouter;
export const { createRouter, createWebHashHistory } = window.VueRouter;

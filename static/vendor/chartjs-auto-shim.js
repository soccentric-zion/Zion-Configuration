// Shim so PrimeVue's Chart component can `import("chart.js/auto")` in the
// no-build / air-gapped setup. chart.umd.js (loaded as a classic <script> in
// index.html) sets the global `window.Chart`; we simply re-export it as the
// default ES module export. An import map in index.html maps the bare
// "chart.js/auto" specifier to this file.
export default window.Chart;

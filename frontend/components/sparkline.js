// Vanilla canvas sparkline — a progressive rolling line chart with no external
// dependency (spec §2: vanilla JS, < 500 KB, no node/build). Feed it new values
// via `.push(value)`; it keeps the last `maxPoints` and redraws on every push
// and on container resize. Replaces the earlier Chart.js wrapper, which
// referenced a `/vendor/chart.umd.js` file that was never committed (so
// `window.Chart` was undefined and the monitoring page threw on load).

function resolveColor(color) {
  if (typeof color !== 'string') return '#0ea5e9';
  if (color.startsWith('var(')) {
    const name = color.slice(4, -1).trim();
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || '#0ea5e9';
  }
  return color;
}

export function Sparkline({ height = 48, maxPoints = 60, color = 'var(--primary)', fill = false, label = 'Telemetry history' } = {}) {
  const container = document.createElement('div');
  container.className = 'spark-wrap';
  container.style.height = `${height}px`;
  const canvas = document.createElement('canvas');
  canvas.setAttribute('role', 'img');
  canvas.setAttribute('aria-label', label);
  canvas.className = 'spark';
  container.appendChild(canvas);
  const ctx = canvas.getContext('2d');
  const values = [];
  let stroke = resolveColor(color);
  let raf = 0;
  let destroyed = false;

  // Re-read the CSS variable on resize so theme changes retune the stroke.
  function refreshColor() { stroke = resolveColor(color); }

  function resize() {
    const w = container.clientWidth;
    const h = container.clientHeight;
    if (!w || !h) return;
    const dpr = window.devicePixelRatio || 1;
    const targetW = Math.round(w * dpr);
    const targetH = Math.round(h * dpr);
    if (canvas.width !== targetW || canvas.height !== targetH) {
      canvas.width = targetW;
      canvas.height = targetH;
    }
  }

  function draw() {
    raf = 0;
    resize();
    const w = canvas.width;
    const h = canvas.height;
    if (!w || !h) return;
    ctx.clearRect(0, 0, w, h);
    if (values.length < 2) {
      // Not enough points to draw a line; still mark the lone point if present.
      if (values.length === 1) pointAt(w, h, 0, values[0]);
      return;
    }
    let min = Infinity, max = -Infinity;
    for (const v of values) { if (v < min) min = v; if (v > max) max = v; }
    if (min === max) { min -= 1; max += 1; }
    const pad = (max - min) * 0.08; // ~grace, matches the old y axis padding
    min -= pad; max += pad;
    const span = max - min || 1;
    const dpr = window.devicePixelRatio || 1;
    const lw = 2.25 * dpr;
    const xAt = i => (i / (maxPoints - 1)) * (w - 6 * dpr) + 3 * dpr;
    const yAt = v => h - 3 * dpr - ((v - min) / span) * (h - 6 * dpr);

    if (fill) {
      ctx.beginPath();
      ctx.moveTo(xAt(0), h);
      for (let i = 0; i < values.length; i++) ctx.lineTo(xAt(i), yAt(values[i]));
      ctx.lineTo(xAt(values.length - 1), h);
      ctx.closePath();
      ctx.fillStyle = hexAlpha(stroke, 0.12);
      ctx.fill();
    }

    ctx.beginPath();
    ctx.moveTo(xAt(0), yAt(values[0]));
    for (let i = 1; i < values.length; i++) ctx.lineTo(xAt(i), yAt(values[i]));
    ctx.strokeStyle = stroke;
    ctx.lineWidth = lw;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    ctx.stroke();

    pointAt(w, h, values.length - 1, values[values.length - 1]);

    function pointAt(_w, _h, i, v) {
      const x = xAt(i), y = yAt(v);
      ctx.beginPath();
      ctx.arc(x, y, 3 * dpr, 0, Math.PI * 2);
      ctx.fillStyle = stroke;
      ctx.fill();
      ctx.lineWidth = 2 * dpr;
      ctx.strokeStyle = '#ffffff';
      ctx.stroke();
    }
  }

  function schedule() {
    if (raf || destroyed) return;
    raf = requestAnimationFrame(draw);
  }

  function push(v) {
    if (typeof v !== 'number' || Number.isNaN(v)) return;
    values.push(v);
    if (values.length > maxPoints) values.shift();
    schedule();
  }

  const ro = new ResizeObserver(() => { refreshColor(); schedule(); });
  ro.observe(container);
  // Initial paint once laid out.
  requestAnimationFrame(() => { refreshColor(); schedule(); });

  return {
    canvas: container,
    push,
    destroy: () => { destroyed = true; if (raf) cancelAnimationFrame(raf); ro.disconnect(); },
    get values() { return values.slice(); },
  };
}

// Add an alpha channel to a #rrggbb color (used for the fill). Falls back to a
// translucent neutral for non-hex colors so fill never throws.
function hexAlpha(hex, alpha) {
  if (typeof hex === 'string' && hex.startsWith('#') && hex.length === 7) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }
  return `rgba(14,165,233,${alpha})`;
}
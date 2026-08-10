// Monitoring page (spec §4.5): live vitals via the daemon's SSE endpoint
// (spec §3 — one SSE endpoint for dashboard push), with an initial REST
// fetch for a fast first paint and rolling sparkline history per metric.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Field } from '../components/field.js';
import { Button } from '../components/button.js';
import { ActionList, ActionRow } from '../components/action-row.js';
import { Sparkline } from '../components/sparkline.js';

function fmtBytes(b) {
  if (!b) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB']; let i = 0; let n = b;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(1)} ${u[i]}`;
}

// A metric tile that owns its own sparkline and updates in place rather
// than being torn down and rebuilt on every SSE tick (keeps the chart
// history alive and avoids visible flicker on a several-times-a-second feed).
function metricTile(label) {
  const valueEl = el('div', { class: 'tile-value' }, '—');
  const spark = Sparkline({ height: 42, label: `${label} history` });
  const node = el('div', { class: 'card' },
    el('div', { class: 'card-body' }, el('h3', {}, label), el('div', { class: 'tile-sub' }, 'Live telemetry'), valueEl, spark.canvas));
  return {
    node,
    update(text, numericValue) {
      valueEl.textContent = text;
      if (numericValue != null) spark.push(numericValue);
    },
  };
}

export async function render(api, store) {
  const cpu = metricTile('CPU load (1m)');
  const mem = metricTile('Memory');
  const disk = metricTile('Physical storage');
  const swap = metricTile('Swap');
  const temp = metricTile('Hottest');
  const uptimeEl = el('div', { class: 'tile-value' }, '—');
  const bootCountEl = el('div', { class: 'tile-sub', style: 'margin-top:6px' }, 'Boots: —');
  const bootReasonEl = el('div', { class: 'tile-sub' }, 'Last boot: —');
  const uptimeTile = el('div', { class: 'card' }, el('div', { class: 'card-body' }, el('h3', {}, 'Uptime'), el('div', { class: 'tile-sub' }, 'Time since last boot'), uptimeEl, bootCountEl, bootReasonEl));
  const netTiles = new Map();
  const netHost = el('div', { class: 'card-grid' });

  const liveDot = el('span', { class: 'live-dot' });
  const liveLabel = el('span', { class: 'muted text-sm' }, 'Connecting…');

  function ensureNetTiles(iface) {
    if (!netTiles.has(iface)) {
      // Spec §4.5: per-interface traffic counters from /sys/class/net/*/statistics
      // — show both rx and tx, each with its own rolling sparkline.
      const rx = metricTile(`${iface} rx`);
      const tx = metricTile(`${iface} tx`);
      netTiles.set(iface, { rx, tx });
      netHost.appendChild(rx.node);
      netHost.appendChild(tx.node);
    }
    return netTiles.get(iface);
  }

  function renderVitals(v) {
    cpu.update(v.load && v.load[0] != null ? v.load[0].toFixed(2) : '—', v.load && v.load[0]);

    const memPct = v.mem_total_kb ? ((1 - v.mem_available_kb / v.mem_total_kb) * 100) : null;
    mem.update(memPct != null ? `${memPct.toFixed(0)}%` : '—', memPct);

    const diskPct = (v.mounts || []).filter(m => m.physical).map(m => m.total_bytes ? ((1 - m.avail_bytes / m.total_bytes) * 100) : 0)
      .reduce((a, b) => Math.max(a, b), 0);
    disk.update(diskPct ? `${diskPct.toFixed(0)}%` : '—', diskPct);

    const swapPct = v.swap_total_bytes ? ((1 - v.swap_free_bytes / v.swap_total_bytes) * 100) : null;
    swap.update(swapPct != null ? `${swapPct.toFixed(0)}%` : '—', swapPct);

    const maxTemp = (v.temperatures || []).map(t => t.temp_millideg_c / 1000).reduce((a, b) => Math.max(a, b), -Infinity);
    temp.update(Number.isFinite(maxTemp) ? `${maxTemp.toFixed(0)}°C` : '—', Number.isFinite(maxTemp) ? maxTemp : null);

    uptimeEl.textContent = v.uptime_secs ? `${Math.round(v.uptime_secs / 60)} min` : '—';
    bootCountEl.textContent = `Boots: ${v.boot_count != null ? v.boot_count : '—'}`;
    bootReasonEl.textContent = `Last boot: ${v.last_boot_reason || '—'}`;

    for (const n of v.net || []) {
      const t = ensureNetTiles(n.iface);
      t.rx.update(fmtBytes(n.rx_bytes), n.rx_bytes / 1024);
      t.tx.update(fmtBytes(n.tx_bytes), n.tx_bytes / 1024);
    }
  }

  try {
    renderVitals(await api.get('/api/v1/monitoring/vitals'));
  } catch (_) { /* SSE below will populate once it connects */ }

  const source = api.eventSource('/api/v1/events');
  source.addEventListener('vitals', (e) => {
    liveDot.classList.add('live'); liveLabel.textContent = 'Live';
    try { renderVitals(JSON.parse(e.data)); } catch (_) { /* skip malformed tick */ }
  });
  source.addEventListener('error', () => { liveDot.classList.remove('live'); liveLabel.textContent = 'Reconnecting…'; });
  store.registerCleanup(() => source.close());

  // ---------- Service health strip (spec §4.5) ----------
  // One glanceable badge per manifest-allowlisted service: active=green,
  // failed=red, inactive=neutral, anything else=warning. Snapshot on load.
  const healthHost = el('div', { class: 'badge-row' });
  function healthBadgeClass(state) {
    if (state === 'active') return 'badge badge-success';
    if (state === 'failed') return 'badge badge-destructive';
    if (state === 'inactive') return 'badge badge-neutral';
    return 'badge badge-warning';
  }
  async function loadServiceHealth() {
    try {
      const rows = await api.get('/api/v1/services/health');
      if (!rows.length) {
        healthHost.replaceChildren(el('p', { class: 'muted' }, 'No services declared for this platform.'));
        return;
      }
      healthHost.replaceChildren(...rows.map(r =>
        el('span', { class: healthBadgeClass(r.state), title: r.state },
          `${r.unit} · ${r.state}`)));
    } catch (_) { healthHost.replaceChildren(el('p', { class: 'error-text' }, 'Service health unavailable.')); }
  }
  loadServiceHealth();

  // ---------- eMMC wear ----------
  const dev = Field({ label: 'Block device', value: 'mmcblk0', placeholder: 'mmcblk0' });
  const wearResult = el('div', { class: 'tile-sub', style: 'margin-top:8px' });
  const emmcCard = Card({
    title: 'eMMC wear', subtitle: 'Reads the device life-time estimate (spec §4.5).',
    footer: ActionList(ActionRow('Check wear', 'Read the eMMC lifetime estimate from the selected block device.', Button({
      variant: 'outline',
      onClick: async () => {
        const r = await api.get(`/api/v1/monitoring/emmc/${encodeURIComponent(dev.get())}`);
        wearResult.textContent = r.life_time_estimate ? `Life-time estimate: ${r.life_time_estimate}` : 'No wear data reported for this device.';
      },
    }, 'Check wear'))),
  }, dev.wrap, wearResult);

  return el('div', {},
    el('div', { class: 'page-header row', style: 'justify-content:space-between' },
      el('div', {}, el('h1', {}, 'Monitoring'), el('p', {}, 'Live vitals, pushed over SSE.')),
      el('div', { class: 'row' }, liveDot, liveLabel)),
    Card({ title: 'Vitals', subtitle: 'Live CPU, memory, physical storage, swap, temperature, and uptime readings.' }, el('div', { class: 'card-grid' }, cpu.node, mem.node, disk.node, swap.node, uptimeTile, temp.node)),
    Card({ title: 'Network throughput', subtitle: 'Per-interface receive throughput from the live telemetry stream.' }, netHost),
    Card({ title: 'Service health', subtitle: 'Allowlisted services and their current systemd state (spec §4.5).' }, healthHost),
    emmcCard);
}

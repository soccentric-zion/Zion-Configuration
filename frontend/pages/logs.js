// Logs page (spec §4.6): journal viewer, journald storage config, audit log,
// and a full journal export download.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Field } from '../components/field.js';
import { Toggle } from '../components/toggle.js';
import { Button } from '../components/button.js';
import { ActionList, ActionRow } from '../components/action-row.js';
import { Table } from '../components/table.js';

export async function render(api, store) {
  let cfg;
  try {
    cfg = await api.get('/api/v1/logs/config');
  } catch (e) {
    cfg = { storage: 'volatile', max_size_bytes: 64 * 1024 * 1024, retention_days: 30 };
    store.toast(`Could not load log configuration: ${e.message}`, 'error');
  }
  let sources = [];
  try {
    sources = await api.get('/api/v1/logs/sources');
  } catch (e) {
    store.toast(`Could not load log sources: ${e.message}`, 'error');
  }

  const sourceRows = (Array.isArray(sources) ? sources : []).map(source => [
    el('strong', {}, source.label || source.id || 'Unknown'),
    el('code', {}, source.id || '—'),
    el('span', { class: 'source-description' }, source.description || '—'),
    el('span', { class: source.available ? 'status-badge status-ok' : 'status-badge' }, source.available ? 'Available' : 'Unavailable'),
  ]);
  const sourceList = Table({
    columns: ['Source', 'Identifier', 'Description', 'Status'],
    caption: 'Available log sources',
  }, sourceRows).element;
  const sourcesCard = Card({
    title: 'Available log sources',
    subtitle: 'Sources exposed by this Linux image and the WebConfig daemon.',
  }, sourceList);

  // ---------- Journal viewer ----------
  const unit = Field({ label: 'Unit', placeholder: 'e.g. webconfigd.service' });
  const priority = Field({ label: 'Priority (0-7)', placeholder: '0=emerg … 7=debug' });
  const limit = Field({ label: 'Limit', type: 'number', value: '200' });
  const pre = el('pre', { class: 'log-viewer' });

  async function loadLogs() {
    const q = new URLSearchParams();
    if (unit.get()) q.set('unit', unit.get());
    if (priority.get()) q.set('priority', priority.get());
    if (limit.get()) q.set('limit', limit.get());
    const r = await api.get(`/api/v1/logs?${q.toString()}`);
    pre.textContent = (r.lines || []).join('\n');
  }

  // Follow mode via SSE (spec §4.6): while on, append new journal lines as they
  // arrive. The backend streams a cursor-based `journal` event (new lines only,
  // no dupes); we render the MESSAGE field of each JSON line.
  let followSource = null;
  function stopFollow() {
    if (followSource) { followSource.close(); followSource = null; }
  }
  function startFollow() {
    stopFollow();
    followSource = api.eventSource('/api/v1/events');
    followSource.addEventListener('journal', (e) => {
      let lines = [];
      try { lines = JSON.parse(e.data); } catch (_) { return; }
      const rendered = (Array.isArray(lines) ? lines : [])
        .map(l => { try { return JSON.parse(l).MESSAGE ?? l; } catch (_) { return l; } });
      if (rendered.length) {
        pre.textContent += (pre.textContent ? '\n' : '') + rendered.join('\n');
        pre.scrollTop = pre.scrollHeight;
      }
    });
  }
  const followToggle = Toggle({
    label: 'Follow (live)', checked: false,
    onChange: v => v ? startFollow() : stopFollow()
  });
  store.registerCleanup(stopFollow);

  const viewerCard = Card({
    title: 'System logs',
    subtitle: 'Filter and inspect recent journal lines from the device, or follow live over SSE.',
    footer: ActionList(
      ActionRow('Download export', 'Save the journal as a text file. With a unit set, exports just that unit (spec §4.6 per-unit export).', Button({
        variant: 'outline',
        onClick: async () => {
          const q = new URLSearchParams();
          if (unit.get()) q.set('unit', unit.get());
          const r = await api.get(`/api/v1/logs/download?${q.toString()}`);
          const name = unit.get() ? `${unit.get().replace(/[^A-Za-z0-9._-]/g, '_')}.log` : 'journal-export.log';
          downloadText(name, r.export || '');
        },
      }, 'Download export')),
      ActionRow('Clear viewer', 'Remove the currently displayed log lines without changing stored logs.', Button({ variant: 'outline', onClick: () => { pre.textContent = ''; } }, 'Clear')),
      ActionRow('Load logs', 'Fetch journal lines using the selected unit, priority, and limit filters.', Button({ onClick: loadLogs }, 'Load'))),
  }, el('div', { class: 'field-row' }, unit.wrap, priority.wrap, limit.wrap), followToggle.wrap, pre);

  // ---------- Journald config ----------
  const storage = Field({
    label: 'Storage', type: 'select', value: cfg.storage,
    options: [{ value: 'volatile', label: 'Volatile (ramdisk)' }, { value: 'persistent', label: 'Persistent (eMMC)' }],
  });
  const maxSize = Field({ label: 'Max size when persistent (bytes)', type: 'number', value: cfg.max_size_bytes });
  const retentionDays = Field({ label: 'Retention length (days)', type: 'number', value: cfg.retention_days || 30, help: 'Journald removes entries older than this limit. Use 0 for the system default.' });
  const configCard = Card({
    title: 'Log rotation',
    subtitle: 'Set retention by age and size for the local journal.',
    footer: ActionList(ActionRow('Save and apply', 'Apply the local journald storage settings.', Button({
      onClick: async () => {
        await api.put('/api/v1/logs/config', {
          ...cfg,
          storage: storage.get(),
          max_size_bytes: Number(maxSize.get()) || 0,
          retention_days: Number(retentionDays.get()) || 0,
        });
        store.toast('Logging config applied', 'success');
      },
    }, 'Save & apply'))),
  }, storage.wrap, maxSize.wrap, retentionDays.wrap);

  // ---------- Audit log ----------
  const auditPre = el('pre', { class: 'log-viewer' });
  async function loadAudit() {
    try {
      const entries = await api.get('/api/v1/logs/audit');
      auditPre.textContent = (entries || []).map(e => `${e.ts}  ${e.user}  ${e.action}${e.detail ? '  ' + e.detail : ''}`).join('\n');
    } catch (e) {
      auditPre.textContent = `Unable to load audit log: ${e.message}`;
      store.toast(`Could not load audit log: ${e.message}`, 'error');
    }
  }
  await loadAudit();
  const auditCard = Card({
    title: 'Audit log',
    subtitle: 'Administrative actions recorded by WebConfig. The audit log is append-only (spec §4.8) — it is not deletable from the UI; only a full factory reset wipes it.',
    footer: ActionList(
      ActionRow('Refresh audit log', 'Reload the recorded administrative actions from the device.', Button({ variant: 'outline', onClick: loadAudit }, 'Refresh')),
    ),
  }, auditPre);

  return el('div', {},
    el('div', { class: 'page-header' }, el('h1', {}, 'Logs'), el('p', {}, 'Journal viewer, local storage policy, and the audit trail.')),
    sourcesCard, viewerCard, configCard, auditCard);
}

function downloadText(name, text) {
  const blob = new Blob([text], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = el('a', { href: url, download: name });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

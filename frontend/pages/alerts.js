// Alerts page (spec §4.7): threshold rules with duration + history, plus
// webhook POST / MQTT event-topic push actions and the per-platform default
// rule packs shipped in the board manifest.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Field } from '../components/field.js';
import { Toggle } from '../components/toggle.js';
import { Button } from '../components/button.js';
import { Table } from '../components/table.js';
import { EmptyState } from '../components/empty-state.js';
import { ActionList, ActionRow } from '../components/action-row.js';

export async function render(api, store) {
  const data = await api.get('/api/v1/alerts');
  const rules = (data.rules || []).slice();
  const webhooks = (data.webhooks || []).slice();
  let mqttEventTopic = data.mqtt_event_topic || '';
  const enabled = Toggle({ label: 'Enable alerting', checked: data.enabled === true });

  // ---------- Default rule packs (read-only, from the board manifest) ----
  const defaults = data.default_rules || [];
  const defaultsCard = Card({
    title: 'Built-in default rules',
    subtitle: 'Per-platform rule packs shipped with the image (spec §4.7). They are always active unless you override or disable one by id below.',
  }, defaults.length
    ? Table({ columns: ['ID', 'Metric', 'Op', 'Threshold', 'Hold (s)', 'Actions'], caption: 'Built-in default rules' },
        defaults.map(r => [r.id, r.metric, r.comparison, String(r.threshold), String(r.duration_secs || 0), (r.actions || []).join(', ')])).element
    : EmptyState({ title: 'No default rule packs for this platform' }));

  // ---------- Rules table ----------------------------------------------
  const tableHost = el('div');
  function renderRules() {
    const rows = rules.map((r, idx) => {
      const ruleEnabled = Toggle({ label: 'Enabled', checked: r.enabled !== false });
      ruleEnabled.input.addEventListener('change', () => { r.enabled = ruleEnabled.get(); });
      return [
        r.id, r.metric, r.comparison, String(r.threshold), String(r.duration_secs || 0), (r.actions || []).join(', '),
        ruleEnabled.wrap,
        Button({ variant: 'link', size: 'sm', title: 'Remove this alert rule from the unsaved list', onClick: () => { rules.splice(idx, 1); renderRules(); } }, 'Delete'),
      ];
    });
    const content = rows.length
      ? Table({ columns: ['ID', 'Metric', 'Op', 'Threshold', 'Hold (s)', 'Actions', 'Enabled', ''], caption: 'Alert rules' }, rows).element
      : EmptyState({ title: 'No alert rules yet' });
    tableHost.replaceChildren(content);
  }
  renderRules();

  // ---------- Add-rule form (with per-rule push actions) ----------------
  const id = Field({ label: 'ID' });
  const metric = Field({ label: 'Metric', value: 'temp' });
  const op = Field({ label: 'Comparison', type: 'select', value: '>', options: ['>', '<', '>=', '<=', '==', '!='].map(v => ({ value: v, label: v })) });
  const thr = Field({ label: 'Threshold', type: 'number', value: '85' });
  const dur = Field({ label: 'Hold (s)', type: 'number', value: '60' });
  const actUi = Toggle({ label: 'UI + event log (always)', checked: true });
  const actWebhook = Toggle({ label: 'Webhook POST', checked: false });
  const actMqtt = Toggle({ label: 'MQTT event topic', checked: false });

  const addCard = Card({ title: 'Add rule', subtitle: 'Define a metric threshold and how long it must hold before an alert fires. UI red-state + event log always apply; enable webhook/MQTT for push notifications.' },
    el('div', { class: 'field-row' }, id.wrap, metric.wrap, op.wrap, thr.wrap, dur.wrap),
    el('div', { class: 'field-row' }, actUi.wrap, actWebhook.wrap, actMqtt.wrap),
    ActionList(ActionRow('Add alert rule', 'Add this threshold rule to the local list; save it below to activate it.', Button({
      variant: 'outline',
      onClick: () => {
        if (!id.get()) { store.toast('ID is required', 'error'); return; }
        const actions = [];
        if (actUi.get()) actions.push('ui');
        if (actWebhook.get()) actions.push('webhook');
        if (actMqtt.get()) actions.push('mqtt');
        rules.push({ id: id.get(), enabled: true, metric: metric.get(), comparison: op.get(), threshold: Number(thr.get()), duration_secs: Number(dur.get()) || 0, actions });
        renderRules();
        id.set(''); thr.set('85'); dur.set('60');
      },
    }, 'Add to list'))));

  // ---------- Notification targets (webhook + MQTT event topic) ---------
  const webhookHost = el('div', { class: 'stack' });
  const newHook = Field({ label: 'Webhook URL', placeholder: 'https://hooks.example.com/alerts' });
  function renderWebhooks() {
    webhookHost.replaceChildren(webhooks.length
      ? el('div', { class: 'list-stack' }, ...webhooks.map((url, idx) => el('div', { class: 'row list-row' },
          el('div', { class: 'mono', style: 'word-break:break-all' }, url),
          Button({ variant: 'link', size: 'sm', onClick: () => { webhooks.splice(idx, 1); renderWebhooks(); } }, 'Remove'))))
      : EmptyState({ title: 'No webhook targets' }));
  }
  renderWebhooks();
  const mqttTopic = Field({ label: 'MQTT event topic base', value: mqttEventTopic, placeholder: 'soccentric/events', help: 'Fired events are published to <base>/<rule_id>.' });
  mqttTopic.input.addEventListener('change', () => { mqttEventTopic = mqttTopic.get(); });

  const notifyCard = Card({
    title: 'Notification targets',
    subtitle: 'Where push actions deliver fired/cleared events (spec §4.7). Webhook targets receive a JSON POST; MQTT events publish to the broker configured under Services → MQTT.',
  }, webhookHost, el('div', { class: 'field-row' }, newHook.wrap, Button({ variant: 'outline', onClick: () => {
    const u = newHook.get().trim();
    if (!u) { store.toast('Enter a webhook URL', 'error'); return; }
    webhooks.push(u); newHook.set(''); renderWebhooks();
  } }, 'Add webhook')), mqttTopic.wrap);

  // ---------- Save ------------------------------------------------------
  const rulesCard = Card({
    title: 'Alert rules', subtitle: 'Review the pending threshold rules before saving them to the device.', footer: ActionList(ActionRow('Save all rules', 'Persist the current alert rules and notification settings.', Button({
      onClick: async () => {
        await api.put('/api/v1/alerts', { enabled: enabled.get(), rules, webhooks, mqtt_event_topic: mqttEventTopic || null });
        store.toast('Alert rules saved', 'success');
      },
    }, 'Save all'))),
  }, enabled.wrap, tableHost);

  // ---------- History ---------------------------------------------------
  const historyItems = (data.history || []).slice(-20).reverse();
  // Spec §4.7: alert history is viewable AND exportable. Download the full
  // ring buffer from the dedicated /alerts/history endpoint (not the truncated
  // slice carried on /alerts) as a client-side file — no backend change.
  function downloadHistory(text, filename, mime) {
    const blob = new Blob([text], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = el('a', { href: url, download: filename });
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }
  async function exportHistory(format) {
    try {
      const rows = await api.get('/api/v1/alerts/history');
      if (!rows.length) { store.toast('No alert history to export', 'error'); return; }
      if (format === 'csv') {
        const head = 'ts,rule_id,state,value\n';
        const body = rows.map(r => `${r.ts},${r.rule_id},${r.state},${r.value}`).join('\n');
        downloadHistory(head + body, 'alert-history.csv', 'text/csv');
      } else {
        downloadHistory(JSON.stringify(rows, null, 2), 'alert-history.json', 'application/json');
      }
      store.toast('History exported', 'success');
    } catch (error) { store.toast(error.message || 'Export failed', 'error'); }
  }
  const historyCard = Card({
    title: 'Recent events',
    subtitle: 'The most recent alert transitions recorded by the daemon.',
    footer: ActionList(
      ActionRow('Export JSON', 'Download the full alert history ring buffer as JSON.', Button({ variant: 'outline', size: 'sm', onClick: () => exportHistory('json') }, 'Export JSON')),
      ActionRow('Export CSV', 'Download the full alert history as CSV for spreadsheet review.', Button({ variant: 'outline', size: 'sm', onClick: () => exportHistory('csv') }, 'Export CSV'))),
  },
    historyItems.length
      ? el('div', { class: 'stack' }, ...historyItems.map(ev => el('div', { class: 'row muted text-sm' },
        el('span', { class: 'mono' }, ev.ts), el('span', {}, ev.rule_id), el('span', {}, ev.state), el('span', {}, `val=${ev.value}`))))
      : EmptyState({ title: 'No events recorded yet' }));

  return el('div', {},
    el('div', { class: 'page-header' }, el('h1', {}, 'Alarms'), el('p', {}, 'Threshold rules and alarm firing history.')),
    defaultsCard, rulesCard, addCard, notifyCard, historyCard);
}
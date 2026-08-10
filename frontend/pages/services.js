// Services & Apps page (spec §4.3): curated systemd unit list + start/stop/
// restart/enable and schema-driven app settings (spec
// §3: adding a new app setting requires zero UI code — the descriptor from
// GET /schemas is rendered generically by schema-form.js).

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Toggle } from '../components/toggle.js';
import { Button } from '../components/button.js';
import { Table } from '../components/table.js';
import { StatusDot } from '../components/badge.js';
import { EmptyState } from '../components/empty-state.js';
import { renderSchemaForm } from '../components/schema-form.js';
import { ActionList, ActionRow } from '../components/action-row.js';

export async function render(api, store) {
  const [list, schemas] = await Promise.all([
    api.get('/api/v1/services'),
    api.get('/api/v1/schemas').catch(() => []),
  ]);

  // ---------- Services table (with an expandable per-unit journal row) ----------
  let servicesTable;
  if ((list || []).length) {
    const { element, tbody } = Table({ columns: ['Unit', 'Active', 'Enabled', ''], caption: 'Services' }, []);
    for (const row of list) {
      const unit = row.unit;
      const st = row.status || {};
      const active = st.active;
      const state = active === 'active' ? 'ok' : active === 'failed' ? 'bad' : 'unknown';
      const enabledToggle = Toggle({
        checked: st.enabled === true,
        onChange: async (checked) => {
          await api.put(`/api/v1/services/${encodeURIComponent(unit)}/enabled`, { enabled: checked });
          store.toast(`${unit}: ${checked ? 'enabled' : 'disabled'} on boot`, 'success');
        },
      });
      const act = (verb) => Button({
        variant: 'link', size: 'sm', title: `${verb[0].toUpperCase() + verb.slice(1)} ${unit}`,
        onClick: async () => { await api.post(`/api/v1/services/${encodeURIComponent(unit)}/${verb}`, {}); store.reload(); },
      }, verb[0].toUpperCase() + verb.slice(1));

      const journalPre = el('pre', { class: 'log-viewer', style: 'margin-top:8px' });
      const journalRow = el('tr', { hidden: true }, el('td', { colspan: '4' }, journalPre));
      const journalToggle = Button({
        variant: 'link', size: 'sm', title: `Show recent journal lines for ${unit}`,
        onClick: async () => {
          journalRow.hidden = !journalRow.hidden;
          if (!journalRow.hidden) {
            const r = await api.get(`/api/v1/services/${encodeURIComponent(unit)}/journal`);
            journalPre.textContent = (r.tail || []).join('\n') || 'No recent log lines.';
          }
        },
      }, 'Journal');

      tbody.appendChild(el('tr', {},
        el('td', {}, unit),
        el('td', {}, el('span', { class: 'row' }, StatusDot(state), st.sub || active || '—')),
        el('td', {}, enabledToggle.wrap),
        el('td', {}, el('div', { class: 'table-actions' }, act('start'), act('stop'), act('restart'), journalToggle))));
      tbody.appendChild(journalRow);
    }
    servicesTable = element;
  } else {
    servicesTable = EmptyState({ title: 'No services in the allowlist', description: 'The board manifest defines which units are manageable here.' });
  }

  // ---------- Schema-driven app settings (spec §3) ----------
  const appSettingsCards = await Promise.all((schemas || []).map(async (s) => {
    const values = await api.get(`/api/v1/apps/${encodeURIComponent(s.id)}/settings`).catch(() => ({}));
    const form = renderSchemaForm(s.descriptor, values);
    return Card({
      title: s.descriptor.title || s.id,
      subtitle: s.descriptor.description || 'Configure settings for this application.',
      footer: ActionList(ActionRow('Save app settings', 'Validate and persist this application configuration.', Button({
        onClick: async () => {
          if (!form.validate()) { store.toast('Fix the highlighted fields', 'error'); return; }
          await api.put(`/api/v1/apps/${encodeURIComponent(s.id)}/settings`, form.getValue());
          store.toast(`${s.descriptor.title || s.id} saved`, 'success');
        },
      }, 'Save'))),
    }, form.element);
  }));

  return el('div', {},
    el('div', { class: 'page-header' }, el('h1', {}, 'Services & Apps'), el('p', {}, 'Manage curated system services and application settings.')),
    Card({ title: 'Services', subtitle: 'Curated systemd units allowed by the board manifest; status and boot enablement are shown here.' }, servicesTable),
    ...appSettingsCards);
}

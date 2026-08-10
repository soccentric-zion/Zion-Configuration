// Current hardware faults and alert transitions.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Table } from '../components/table.js';
import { EmptyState } from '../components/empty-state.js';
import { StatusDot } from '../components/badge.js';

export async function render(api) {
    const [vitals, alerts] = await Promise.all([
        api.get('/api/v1/monitoring/vitals'),
        api.get('/api/v1/alerts'),
    ]);
    const thermalFaults = (vitals.temperatures || []).filter(zone => zone.throttled).map(zone => [zone.zone, 'Thermal throttling', 'active']);
    const alertFaults = (alerts.history || []).filter(event => event.state && event.state !== 'clear').slice(-20).reverse().map(event => [event.rule_id, `Alert value ${event.value}`, event.state]);
    const faults = [...thermalFaults, ...alertFaults];
    const content = faults.length
        ? Table({ columns: ['Source', 'Condition', 'State'], caption: 'Active faults' }, faults.map(([source, condition, state]) => [source, condition, el('span', { class: 'row' }, StatusDot(state === 'active' ? 'bad' : 'unknown'), state)])).element
        : EmptyState({ title: 'No active faults reported' });

    return el('div', {},
        el('div', { class: 'page-header' }, el('h1', {}, 'Faults'), el('p', {}, 'Current thermal and alert-derived fault conditions.')),
        Card({ title: 'Active faults', subtitle: 'Faults are read from live telemetry and alert transitions.' }, content));
}

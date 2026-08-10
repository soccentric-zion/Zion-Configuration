// Display inventory and platform-provided display information.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Badge } from '../components/badge.js';
import { EmptyState } from '../components/empty-state.js';
import { peripheralsOf } from './hardware-utils.js';

const DISPLAY_INTERFACES = [
    ['mipi-dsi', 'MIPI DSI', 'Embedded display interface for panels connected directly to the board.'],
    ['hdmi', 'HDMI', 'Digital video output for HDMI monitors and HMI displays.'],
    ['displayport', 'DisplayPort', 'Digital video output for DisplayPort monitors and docks.'],
];

export async function render(api) {
    const data = await api.get('/api/v1/io');
    const panel = await api.get('/api/v1/io/platform-panel').catch(() => ({}));
    const displays = peripheralsOf(data, 'display');
    const panelDisplays = panel.display || panel.displays || [];
    const supportedPorts = new Set(displays.flatMap(display => {
        const ports = display.caps && display.caps.ports;
        return Array.isArray(ports) ? ports.map(port => String(port).toLowerCase()) : [];
    }));
    const interfaceCard = Card({
        title: 'Display interfaces',
        subtitle: 'Port availability is declared by the selected board manifest.',
    }, ...DISPLAY_INTERFACES.map(([id, label, description]) => el('div', { class: 'row' },
        el('div', {}, el('strong', {}, label), el('div', { class: 'field-help' }, description)),
        Badge(supportedPorts.has(id) ? 'available' : 'not declared', supportedPorts.has(id) ? 'success' : 'neutral'))));
    const cards = displays.map(display => Card({ title: display.human_name || display.id, subtitle: display.device },
        el('div', { class: 'row' }, el('span', {}, 'Status'), el('span', { class: 'muted' }, 'Manifest-defined')),
        el('div', { class: 'row' }, el('span', {}, 'Capabilities'), el('span', { class: 'muted' }, Object.keys(display.caps || {}).length ? Object.entries(display.caps).map(([key, value]) => `${key}: ${value}`).join(', ') : 'Board-defined defaults'))));
    const panelRows = panelDisplays.map(display => el('div', { class: 'row' },
        el('strong', {}, display.human_name || display.id || 'Display'),
        el('span', { class: 'muted' }, display.device || JSON.stringify(display))));
    const panelCard = panelDisplays.length
        ? Card({ title: 'Platform displays', subtitle: 'Additional display information reported by the platform panel.' }, ...panelRows)
        : null;

    return el('div', {},
        el('div', { class: 'page-header' }, el('h1', {}, 'Display'), el('p', {}, 'Inspect display hardware and platform-provided display capabilities.')),
        interfaceCard,
        cards.length ? el('div', { class: 'card-grid' }, ...cards) : EmptyState({ title: 'No display hardware reported by this board' }),
        panelCard);
}

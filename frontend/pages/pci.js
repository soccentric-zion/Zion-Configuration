// PCI peripheral inventory.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { EmptyState } from '../components/empty-state.js';
import { Table } from '../components/table.js';

export async function render(api) {
    const data = await api.get('/api/v1/io/pci');
    const devices = Array.isArray(data.devices) ? data.devices : [];
    const table = Table({ columns: ['Address', 'Class', 'Vendor ID', 'Product ID', 'Revision', 'Details'], caption: 'Connected PCI devices' }, devices.map(device => [
        el('code', {}, device.address),
        device.class || '-',
        el('code', {}, device.vendor_id),
        el('code', {}, device.product_id),
        el('code', {}, device.revision || '-'),
        device.description || '-',
    ])).element;
    return el('div', {}, el('div', { class: 'page-header' }, el('h1', {}, 'PCI'), el('p', {}, 'Inspect live PCI devices discovered by lspci.')),
        data.available && devices.length ? Card({ title: 'Connected PCI devices', subtitle: 'Live inventory from lspci.', }, table) : EmptyState({ title: data.error || 'No PCI devices detected' }));
}

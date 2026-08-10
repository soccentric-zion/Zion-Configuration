// USB peripheral inventory.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { EmptyState } from '../components/empty-state.js';
import { Table } from '../components/table.js';

export async function render(api) {
    const data = await api.get('/api/v1/io/usb');
    const devices = Array.isArray(data.devices) ? data.devices : [];
    const table = Table({ columns: ['Bus', 'Device', 'Vendor ID', 'Product ID', 'Description'], caption: 'Connected USB devices' }, devices.map(device => [
        el('code', {}, device.bus),
        el('code', {}, device.device),
        el('code', {}, device.vendor_id),
        el('code', {}, device.product_id),
        device.description || '-',
    ])).element;
    return el('div', {}, el('div', { class: 'page-header' }, el('h1', {}, 'USB'), el('p', {}, 'Inspect live USB devices discovered by lsusb.')),
        data.available && devices.length ? Card({ title: 'Connected USB devices', subtitle: 'Live inventory from lsusb.', }, table) : EmptyState({ title: data.error || 'No USB devices detected' }));
}

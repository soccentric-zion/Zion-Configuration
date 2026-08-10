// Serial peripheral configuration.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Field } from '../components/field.js';
import { Toggle } from '../components/toggle.js';
import { Button } from '../components/button.js';
import { ActionList, ActionRow } from '../components/action-row.js';
import { Table } from '../components/table.js';
import { peripheralsOf, saveIo } from './hardware-utils.js';

export async function render(api, store) {
    const data = await api.get('/api/v1/io');
    const [i2cData, spiData] = await Promise.all([
        api.get('/api/v1/io/i2c'),
        api.get('/api/v1/io/spi'),
    ]);
    const peripherals = peripheralsOf(data, 'serial');
    const subsystemEnabled = Toggle({ label: 'Enable serial subsystem', checked: data.config && data.config.serial_enabled !== false });
    const rows = peripherals.map(peripheral => {
        const existing = (data.config && data.config.serial || []).find(item => item.device === peripheral.device) || {};
        const baud = Field({ label: 'Baud', type: 'number', value: existing.baud || 115200 });
        const mode = Field({ label: 'Mode', type: 'select', value: existing.mode || peripheral.caps?.modes?.[0] || 'rs232', options: (peripheral.caps?.modes || ['rs232']).map(value => ({ value, label: value.toUpperCase() })) });
        const owner = Field({ label: 'Owner', type: 'select', value: existing.owner || 'unassigned', options: ['unassigned', 'console', 'customer', 'modbus'].map(value => ({ value, label: value })) });
        return { peripheral, enabled: Toggle({ label: 'Enable serial port', checked: existing.enabled !== false }), baud, mode, owner };
    });
    const save = async () => { await saveIo(api, data, { serial_enabled: subsystemEnabled.get(), serial: rows.map(row => ({ device: row.peripheral.device, enabled: row.enabled.get(), baud: Number(row.baud.get()) || 115200, mode: row.mode.get(), owner: row.owner.get() })) }); store.toast('Serial settings saved', 'success'); };
    const apply = async () => { await api.post('/api/v1/io/serial/apply', {}); store.toast('Serial applied', 'success'); };
    const i2cTable = Table({ columns: ['Bus', 'Address', 'Status'], caption: 'I2C devices' }, (i2cData.devices || []).map(device => [
        el('code', {}, `i2c-${device.bus}`), el('code', {}, device.address), device.status,
    ])).element;
    const spiTable = Table({ columns: ['Node', 'Bus', 'Chip Select', 'Device', 'Driver', 'Modalias'], caption: 'SPI devices' }, (spiData.devices || []).map(device => [
        el('code', {}, device.name), el('code', {}, device.bus), el('code', {}, device.chip_select),
        el('code', {}, device.device_path), device.driver, el('code', {}, device.modalias),
    ])).element;
    const scanCard = (title, subtitle, table, result) => Card({ title, subtitle: result.error || subtitle }, result.available && result.devices.length ? table : el('p', { class: 'muted' }, result.error || `No ${title.toLowerCase()} detected.`));

    return el('div', {}, el('div', { class: 'page-header' }, el('h1', {}, 'Serial Peripherals'), el('p', {}, 'Configure serial ports and inspect live I2C and SPI buses.')),
        Card({ title: 'Serial subsystem', subtitle: 'Enable serial before configuring individual ports.' }, subsystemEnabled.wrap),
        rows.length ? rows.map(row => Card({ title: row.peripheral.human_name || row.peripheral.device, subtitle: row.peripheral.device }, row.enabled.wrap, row.baud.wrap, row.mode.wrap, row.owner.wrap)) : el('p', { class: 'muted' }, 'No serial peripherals on this board.'),
        rows.length ? Card({ title: 'Serial actions' }, ActionList(
            ActionRow('Save settings', 'Persist the serial peripheral configuration.', Button({ onClick: save }, 'Save settings')),
            ActionRow('Apply serial', 'Apply serial configuration (baud, ownership) to the device.', Button({ variant: 'outline', onClick: apply }, 'Apply serial')))) : null,
        scanCard('I2C devices', 'Live addresses discovered with i2cdetect.', i2cTable, i2cData),
        scanCard('SPI devices', 'Kernel-created spidev nodes; SPI is enumerated without probing unknown devices.', spiTable, spiData));
}

// CAN peripheral configuration.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Field } from '../components/field.js';
import { Toggle } from '../components/toggle.js';
import { Button } from '../components/button.js';
import { ActionList, ActionRow } from '../components/action-row.js';
import { peripheralsOf, saveIo } from './hardware-utils.js';

export async function render(api, store) {
    const data = await api.get('/api/v1/io');
    const peripherals = peripheralsOf(data, 'can');
    const subsystemEnabled = Toggle({ label: 'Enable CAN subsystem', checked: data.config && data.config.can_enabled !== false });
    const rows = peripherals.map(peripheral => {
        const existing = (data.config && data.config.can || []).find(item => item.name === peripheral.device) || {};
        return { peripheral, enabled: Toggle({ label: 'Enable CAN interface', checked: existing.enabled !== false }), bitrate: Field({ label: 'Bitrate', type: 'number', value: existing.bitrate || 500000 }), fd: Toggle({ label: 'CAN FD', checked: existing.fd }), dataBitrate: Field({ label: 'FD data bitrate', type: 'number', value: existing.data_bitrate || 2000000 }), listenOnly: Toggle({ label: 'Listen only', checked: existing.listen_only }), upOnBoot: Toggle({ label: 'Up on boot', checked: existing.up_on_boot !== false }), existing };
    });
    const save = async () => { await saveIo(api, data, { can_enabled: subsystemEnabled.get(), can: rows.map(row => ({ name: row.peripheral.device, enabled: row.enabled.get(), bitrate: Number(row.bitrate.get()) || 500000, fd: row.fd.get(), data_bitrate: Number(row.dataBitrate.get()) || 0, listen_only: row.listenOnly.get(), restart_ms: row.existing.restart_ms || 100, up_on_boot: row.upOnBoot.get() })) }); store.toast('CAN settings saved', 'success'); };
    const apply = async () => { await api.post('/api/v1/io/can/apply', {}); store.toast('CAN applied', 'success'); };

    return el('div', {}, el('div', { class: 'page-header' }, el('h1', {}, 'CAN'), el('p', {}, 'Configure CAN bitrate, FD mode, listen-only behavior, and startup.')),
        Card({ title: 'CAN subsystem', subtitle: 'Enable CAN before configuring individual interfaces.' }, subsystemEnabled.wrap),
        rows.length ? rows.map(row => {
            const terminationGpio = row.peripheral.caps?.termination_gpio;
            return Card({ title: row.peripheral.human_name || row.peripheral.device, subtitle: row.peripheral.device },
                el('div', { class: 'toggle-stack' }, row.enabled.wrap, row.fd.wrap, row.listenOnly.wrap, row.upOnBoot.wrap),
                row.bitrate.wrap, row.dataBitrate.wrap,
                terminationGpio ? Button({
                    variant: 'outline', size: 'sm',
                    onClick: async () => {
                        const result = await api.post(`/api/v1/io/gpio/${encodeURIComponent(terminationGpio)}/toggle`, {});
                        store.toast(`Termination → ${result.value ? 'on' : 'off'}`, 'success');
                    },
                }, 'Toggle termination') : null);
        }) : el('p', { class: 'muted' }, 'No CAN interfaces on this board.'),
        rows.length ? Card({ title: 'CAN actions' }, ActionList(ActionRow('Save settings', 'Persist CAN configuration.', Button({ onClick: save }, 'Save settings')), ActionRow('Apply CAN', 'Apply CAN configuration to the device.', Button({ variant: 'outline', onClick: apply }, 'Apply CAN')))) : null);
}

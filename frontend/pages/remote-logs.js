// Remote Logs page: configure forwarding of journald entries to a syslog server.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Field } from '../components/field.js';
import { Toggle } from '../components/toggle.js';
import { Button } from '../components/button.js';
import { ActionList, ActionRow } from '../components/action-row.js';

export async function render(api, store) {
    const cfg = await api.get('/api/v1/logs/config');
    const remote = cfg.remote_syslog || {};
    const enabled = Toggle({ label: 'Enable remote syslog', checked: remote.enabled === true });
    const host = Field({ label: 'Host', value: remote.host || '', placeholder: 'logs.example.com' });
    const port = Field({ label: 'Port', type: 'number', value: remote.port || 514 });
    const protocol = Field({
        label: 'Protocol',
        type: 'select',
        value: remote.protocol || 'udp',
        options: [{ value: 'udp', label: 'UDP' }, { value: 'tcp', label: 'TCP' }],
    });
    const result = el('div', { class: 'field-help' }, 'Changes apply to the journald forwarding service.');

    async function save() {
        // Always send the host/port/protocol the operator entered, toggling
        // only `enabled` — sending null on disable used to discard the
        // remembered destination entirely, forcing it to be re-typed to
        // re-enable later.
        await api.put('/api/v1/logs/config', {
            ...cfg,
            remote_syslog: {
                enabled: enabled.get(),
                host: host.get(),
                port: Number(port.get()) || 514,
                protocol: protocol.get(),
            },
        });
        result.textContent = 'Remote log forwarding settings applied.';
        store.toast('Remote log settings applied', 'success');
    }

    return el('div', {},
        el('div', { class: 'page-header' },
            el('h1', {}, 'Remote Logs'),
            el('p', {}, 'Forward device journal entries to an external syslog server.')),
        Card({
            title: 'Remote syslog forwarding',
            subtitle: 'The daemon forwards journal messages through a managed systemd service. Use UDP for simple delivery or TCP for a connection-oriented stream.',
            footer: ActionList(
                ActionRow('Save and apply', 'Validate and apply the remote forwarding settings.', Button({ onClick: save }, 'Save & apply')),
            ),
        }, enabled.wrap, el('div', { class: 'field-row' }, host.wrap, port.wrap, protocol.wrap), result));
}

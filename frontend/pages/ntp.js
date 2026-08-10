// Network Time Protocol configuration.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Field } from '../components/field.js';
import { Toggle } from '../components/toggle.js';
import { Button } from '../components/button.js';
import { ActionList, ActionRow } from '../components/action-row.js';

export async function render(api, store) {
    const cfg = await api.get('/api/v1/system');
    const enabled = Toggle({ label: 'NTP enabled', checked: cfg.time && cfg.time.ntp_enabled });
    const servers = Field({
        label: 'NTP servers (comma-separated)',
        value: (cfg.ntp_servers || []).join(', '),
        help: 'Servers are tried in the order listed.',
    });

    async function save() {
        await api.put('/api/v1/system', {
            ...cfg,
            time: { ...cfg.time, ntp_enabled: enabled.get() },
            ntp_servers: servers.get().split(',').map(value => value.trim()).filter(Boolean),
        });
        store.toast('NTP settings saved', 'success');
    }

    async function apply() {
        await api.post('/api/v1/system/apply', {});
        store.toast('NTP settings applied', 'success');
    }

    return el('div', { class: 'time-page' },
        el('div', { class: 'page-header' },
            el('h1', {}, 'NTP'),
            el('p', {}, 'Synchronize the device clock with network time servers.')),
        Card({
            title: 'Network Time Protocol',
            subtitle: 'Use NTP when a reachable network time source is available.',
            footer: ActionList(
                ActionRow('Apply to device', 'Apply clock synchronization settings now.', Button({ variant: 'outline', onClick: apply }, 'Apply')),
                ActionRow('Save settings', 'Store the NTP policy and server list.', Button({ onClick: save }, 'Save')),
            ),
        }, el('div', { class: 'form-grid' }, enabled.wrap, servers.wrap)),
    );
}

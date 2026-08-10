// Precision Time Protocol configuration.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Field } from '../components/field.js';
import { Toggle } from '../components/toggle.js';
import { Button } from '../components/button.js';
import { ActionList, ActionRow } from '../components/action-row.js';

export async function render(api, store) {
    const cfg = await api.get('/api/v1/system');
    const enabled = Toggle({ label: 'PTP enabled', checked: cfg.time && cfg.time.ptp_enabled });
    const iface = Field({
        label: 'PTP network interface',
        value: cfg.time && cfg.time.ptp_interface,
        placeholder: 'eth0',
        help: 'Interface connected to the PTP grandmaster.',
    });
    const domain = Field({
        label: 'PTP domain',
        type: 'number',
        value: cfg.time && cfg.time.ptp_domain,
        help: 'PTP domain number, normally 0.',
    });

    async function save() {
        await api.put('/api/v1/system', {
            ...cfg,
            time: {
                ...cfg.time,
                ptp_enabled: enabled.get(),
                ptp_interface: iface.get() || null,
                ptp_domain: domain.get() ? Number(domain.get()) : null,
            },
        });
        store.toast('PTP settings saved', 'success');
    }

    async function apply() {
        await api.post('/api/v1/system/apply', {});
        store.toast('PTP settings applied', 'success');
    }

    return el('div', { class: 'time-page' },
        el('div', { class: 'page-header' },
            el('h1', {}, 'PTP'),
            el('p', {}, 'Configure precision clock synchronization from a PTP grandmaster.')),
        Card({
            title: 'Precision Time Protocol',
            subtitle: 'PTP takes precedence over NTP when enabled.',
            footer: ActionList(
                ActionRow('Apply to device', 'Apply clock synchronization settings now.', Button({ variant: 'outline', onClick: apply }, 'Apply')),
                ActionRow('Save settings', 'Store the PTP interface and domain.', Button({ onClick: save }, 'Save')),
            ),
        }, el('div', { class: 'form-grid' }, enabled.wrap, iface.wrap, domain.wrap)),
    );
}

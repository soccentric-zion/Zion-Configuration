// Telemetry publishing policy page.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Field } from '../components/field.js';
import { Toggle } from '../components/toggle.js';
import { Button } from '../components/button.js';
import { ActionList, ActionRow } from '../components/action-row.js';

export async function render(api, store) {
    const telemetry = await api.get('/api/v1/monitoring/telemetry');
    const enabled = Toggle({ label: 'Enable telemetry publishing', checked: telemetry.enabled });
    const serverUrl = Field({ label: 'Telemetry server URL', value: telemetry.server_url || '', placeholder: 'https://telemetry.example.com/v1/ingest' });
    const username = Field({ label: 'Login', value: telemetry.username || '' });
    const password = Field({ label: 'Password', type: 'password', value: telemetry.password || '' });
    const diagnostics = Toggle({ label: 'Include diagnostics', checked: telemetry.include_diagnostics });
    const interval = Field({
        label: 'Publish interval (seconds)',
        type: 'number',
        value: telemetry.publish_interval_secs || 30,
        help: 'Minimum interval is 5 seconds.',
    });

    async function save() {
        const publishInterval = Number(interval.get());
        if (!Number.isFinite(publishInterval) || publishInterval < 5) {
            store.toast('Publish interval must be at least 5 seconds', 'error');
            return;
        }
        await api.put('/api/v1/monitoring/telemetry', {
            enabled: enabled.get(),
            server_url: serverUrl.get(),
            username: username.get(),
            password: password.get(),
            publish_interval_secs: publishInterval,
            include_diagnostics: diagnostics.get(),
        });
        store.toast('Telemetry settings saved', 'success');
    }

    return el('div', {},
        el('div', { class: 'page-header' },
            el('h1', {}, 'Telemetry'),
            el('p', {}, 'Control publication of device metrics and diagnostics to the configured cloud service.')),
        Card({
            title: 'Telemetry publishing',
            subtitle: 'Telemetry is independent from local monitoring, which remains available on demand.',
            footer: ActionList(ActionRow(
                'Save telemetry settings',
                'Store the telemetry publishing policy.',
                Button({ onClick: save }, 'Save settings'),
            )),
        }, el('div', { class: 'form-grid' },
            el('div', { class: 'toggle-stack' }, enabled.wrap, diagnostics.wrap),
            serverUrl.wrap,
            username.wrap,
            password.wrap,
            interval.wrap,
        )),
    );
}

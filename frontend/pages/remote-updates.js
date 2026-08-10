// Remote update server configuration and availability checks.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Field } from '../components/field.js';
import { Toggle } from '../components/toggle.js';
import { Button } from '../components/button.js';
import { ActionList, ActionRow } from '../components/action-row.js';

export async function render(api, store) {
    const otaConfig = await api.get('/api/v1/updates/ota');
    const enabled = Toggle({ label: 'Enable remote updates', checked: otaConfig.enabled === true });
    const server = Field({
        label: 'Remote update server URL',
        value: otaConfig.server_url,
        placeholder: 'https://updates.example.com/check',
        help: 'The server should return update_available and optionally version and platform.',
    });
    const status = el('div', { class: 'field-help' }, 'No check performed yet.');

    async function save() {
        await api.put('/api/v1/updates/ota', {
            ...otaConfig,
            enabled: enabled.get(),
            server_url: server.get() || null,
        });
        store.toast('Remote update settings saved', 'success');
    }

    async function check() {
        const result = await api.get('/api/v1/updates/ota/check');
        status.textContent = result.checked
            ? (result.update_available
                ? `Update available${result.version ? `: ${result.version}` : ''}.`
                : 'No update is currently available.')
            : (result.reason || 'No update server configured.');
    }

    return el('div', {},
        el('div', { class: 'page-header' },
            el('h1', {}, 'Remote Updates'),
            el('p', {}, 'Configure and check the remote server used for update availability.')),
        Card({
            title: 'Remote update service',
            subtitle: 'Remote checks are notify-only; signed bundles are installed from the local Updates page.',
            footer: ActionList(
                ActionRow('Save server', 'Store the remote update server configuration.', Button({ onClick: save }, 'Save')),
                ActionRow('Check for updates', 'Ask the configured server for compatible update status.', Button({ variant: 'outline', onClick: check }, 'Check now')),
            ),
        }, enabled.wrap, server.wrap, status),
    );
}

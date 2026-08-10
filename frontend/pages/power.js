// Power controls: reboot, shutdown, and factory reset.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Button } from '../components/button.js';
import { confirmDialog } from '../components/modal.js';
import { ActionList, ActionRow } from '../components/action-row.js';

export function render(api, store) {
    const reboot = Button({
        variant: 'outline', icon: 'power',
        onClick: async () => {
            if (!await confirmDialog({
                title: 'Reboot device?',
                description: 'The device will restart. The WebConfig connection will return when it is ready.',
                confirmLabel: 'Reboot',
            })) return;
            await api.post('/api/v1/system/reboot', {});
            store.showReconnect();
        },
    }, 'Reboot');

    const shutdown = Button({
        variant: 'outline',
        onClick: async () => {
            if (!await confirmDialog({
                title: 'Shut down device?',
                description: 'The device will power off and will need to be started manually.',
                confirmLabel: 'Shut down',
                variant: 'destructive',
            })) return;
            await api.post('/api/v1/system/shutdown', {});
            store.toast('Shutting down');
        },
    }, 'Shutdown');

    const powerCard = Card({
        title: 'Power controls',
        subtitle: 'Restart or power off the device.',
    }, ActionList(
        ActionRow('Reboot', 'Restart the operating system without changing configuration.', reboot),
        ActionRow('Shutdown', 'Power off the device. A person must start it again.', shutdown)));

    return el('div', { class: 'power-page' },
        el('div', { class: 'page-header' },
            el('h1', {}, 'Power'),
            el('p', {}, 'Safely reboot or shut down the device.')),
        powerCard);
}
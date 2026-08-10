// System control page: reset configuration or restore installed defaults.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Button } from '../components/button.js';
import { promptDialog, confirmDialog } from '../components/modal.js';
import { ActionList, ActionRow } from '../components/action-row.js';

export function render(api, store) {
    const systemReset = Button({
        variant: 'outline',
        onClick: async () => {
            const typed = await promptDialog({
                title: 'Reset system settings?',
                label: 'Type RESET to return the device to default settings',
                confirmLabel: 'Reset system',
            });
            if (typed !== 'RESET') {
                store.toast('System reset cancelled');
                return;
            }
            await api.post('/api/v1/system/factory-reset', { confirm: 'RESET', mode: 'settings' });
            store.showReconnect();
        },
    }, 'System reset');

    const factoryReset = Button({
        variant: 'destructive-outline',
        onClick: async () => {
            if (!await confirmDialog({
                title: 'Factory reset device?',
                description: 'This erases all settings and user accounts and returns the device to its installed defaults.',
                confirmLabel: 'Continue',
                variant: 'destructive',
            })) return;
            const typed = await promptDialog({ title: 'Type RESET to confirm', label: 'Confirmation phrase' });
            if (typed !== 'RESET') {
                store.toast('Factory reset cancelled');
                return;
            }
            await api.post('/api/v1/system/factory-reset', { confirm: 'RESET', mode: 'settings+users' });
            store.showReconnect();
        },
    }, 'Factory reset');

    return el('div', { class: 'system-control-page' },
        el('div', { class: 'page-header' },
            el('h1', {}, 'System Control'),
            el('p', {}, 'Reset configuration or return the device to its installed defaults.')),
        Card({
            title: 'System controls',
            subtitle: 'These actions change persistent device state and may require a reconnect.',
        }, ActionList(
            ActionRow('System reset', 'Return configuration to default mode while preserving user accounts.', systemReset),
            ActionRow('Factory reset', 'Erase configuration and user accounts. This cannot be undone.', factoryReset))),
    );
}

// SSH daemon and serial console settings.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Field } from '../components/field.js';
import { Toggle } from '../components/toggle.js';
import { Button } from '../components/button.js';
import { ActionList, ActionRow } from '../components/action-row.js';
import { confirmDialog } from '../components/modal.js';

export async function render(api, store) {
    const systemCfg = await api.get('/api/v1/system');
    const sshd = Toggle({ label: 'SSH daemon enabled', checked: systemCfg.ssh && systemCfg.ssh.sshd_enabled });
    const getty = Toggle({ label: 'Serial getty enabled', checked: systemCfg.ssh && systemCfg.ssh.serial_getty_enabled });
    const authKeys = Field({ label: 'Authorized keys (one per line)', type: 'textarea', value: systemCfg.ssh && systemCfg.ssh.authorized_keys });

    async function apply() {
        // Spec §4.8: disabling sshd (or restarting it via apply) can sever the
        // very session the operator is using. Confirm before applying when the
        // daemon is being disabled — that change is not reversible from SSH.
        if (!sshd.get()) {
            const ok = await confirmDialog({
                title: 'Disable SSH daemon?',
                description: 'Disabling sshd stops the SSH service immediately. Any active SSH session — including this one, if you are connected over SSH — will be disconnected. You will need console or WebConfig access to re-enable it.',
                confirmLabel: 'Disable and apply',
                variant: 'destructive',
            });
            if (!ok) return;
        }
        await api.post('/api/v1/system/apply', {});
        store.toast('System settings applied', 'success');
    }

    return el('div', {},
        el('div', { class: 'page-header' }, el('h1', {}, 'SSH & Console'), el('p', {}, 'Control remote shell access and the serial console login.')),
        Card({
            title: 'SSH and console',
            subtitle: 'Settings are stored with the system configuration.',
            footer: ActionList(
                ActionRow('Apply to device', 'Apply the general system configuration to the running device.', Button({ variant: 'outline', onClick: apply }, 'Apply')),
                ActionRow('Save settings', 'Store SSH and console settings for the next apply.', Button({
                    onClick: async () => {
                        await api.put('/api/v1/system', { ...systemCfg, ssh: { sshd_enabled: sshd.get(), serial_getty_enabled: getty.get(), authorized_keys: authKeys.get() } });
                        store.toast('SSH settings saved', 'success');
                    }
                }, 'Save'))),
        }, el('div', { class: 'form-grid' }, el('div', { class: 'toggle-stack' }, sshd.wrap, getty.wrap), authKeys.wrap)));
}

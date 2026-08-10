// Configuration backup, restore, and diagnostics.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Field } from '../components/field.js';
import { Toggle } from '../components/toggle.js';
import { Button } from '../components/button.js';
import { confirmDialog, promptDialog } from '../components/modal.js';
import { ActionList, ActionRow } from '../components/action-row.js';

function downloadJson(name, obj) {
    const blob = new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = el('a', { href: url, download: name });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function render(api, store) {
    const backupConfig = await api.get('/api/v1/updates/backup/config');
    const includeSecrets = Toggle({ label: 'Include secrets in download' });
    const remoteEnabled = Toggle({ label: 'Enable remote backup connection', checked: backupConfig.enabled });
    const backupEnabled = Toggle({ label: 'Enable backup', checked: backupConfig.backup_enabled !== false });
    const restoreEnabled = Toggle({ label: 'Enable restore', checked: backupConfig.restore_enabled !== false });
    const serverUrl = Field({ label: 'Remote backup server URL', value: backupConfig.server_url, placeholder: 'https://backup.example.com/webconfig', help: 'The connection check sends a GET request to this endpoint.' });
    const connectionStatus = el('div', { class: 'field-help' }, 'Not checked');
    const restoreFile = el('input', { type: 'file', accept: 'application/json', class: 'input' });
    let downloadButton;
    let restoreButton;

    const updateFeatureState = () => {
        downloadButton.disabled = !backupEnabled.get();
        restoreButton.disabled = !restoreEnabled.get();
    };

    downloadButton = Button({
        variant: 'outline', onClick: async () => {
            try {
                if (includeSecrets.get()) {
                    // Secrets only ever leave the device inside a
                    // passphrase-encrypted envelope (spec §4.4). The passphrase
                    // travels in the POST body, never the query string.
                    const passphrase = await promptDialog({
                        title: 'Encrypt backup with secrets',
                        label: 'Passphrase (min 8 characters)',
                        type: 'password',
                        placeholder: 'At least 8 characters',
                        confirmLabel: 'Download',
                    });
                    if (!passphrase) { store.toast('Cancelled', 'info'); return; }
                    if (passphrase.length < 8) { store.toast('Passphrase must be at least 8 characters', 'error'); return; }
                    const r = await api.post('/api/v1/updates/backup', { passphrase });
                    downloadJson('webconfig-backup.json', r);
                } else {
                    const r = await api.get('/api/v1/updates/backup?include_secrets=false');
                    downloadJson('webconfig-backup.json', r);
                }
            } catch (e) { store.toast(e.message, 'error'); }
        }
    }, 'Download config');
    restoreButton = Button({
        variant: 'destructive-outline',
        onClick: async () => {
            const file = restoreFile.files && restoreFile.files[0];
            if (!file) { store.toast('Choose a backup file first', 'error'); return; }
            if (!await confirmDialog({ title: 'Restore config?', description: 'This overwrites the current configuration.', variant: 'destructive' })) return;
            try {
                const parsed = JSON.parse(await file.text());
                let body = parsed;
                if (parsed && parsed.encrypted) {
                    const passphrase = await promptDialog({
                        title: 'Encrypted backup',
                        label: 'Passphrase',
                        type: 'password',
                        confirmLabel: 'Restore',
                    });
                    if (!passphrase) { store.toast('Cancelled', 'info'); return; }
                    body = { ...parsed, passphrase };
                }
                await api.post('/api/v1/updates/restore', body);
                store.toast('Config restored', 'success');
            } catch (e) { store.toast(e.message, 'error'); }
        },
    }, 'Restore');
    backupEnabled.input.addEventListener('change', updateFeatureState);
    restoreEnabled.input.addEventListener('change', updateFeatureState);
    updateFeatureState();

    const card = Card({ title: 'Backup and restore', subtitle: 'Export configuration or diagnostics, or replace the current configuration from a backup.' },
        el('div', { class: 'field-row' },
            el('div', {}, el('div', { class: 'field-label' }, 'Backup contents'), el('div', { class: 'field-help' }, 'Secrets are excluded unless explicitly included.')),
            includeSecrets.wrap),
        ActionList(
            ActionRow('Download configuration', 'Export the current configuration for safekeeping or another device.', downloadButton),
            ActionRow('Download diagnostics', 'Export diagnostic information to investigate a device issue.', Button({
                variant: 'outline', onClick: async () => {
                    const r = await api.get('/api/v1/updates/diagnostics');
                    downloadJson('webconfig-diagnostics.json', r);
                }
            }, 'Diagnostics'))),
        el('label', { class: 'field' }, el('span', { class: 'field-label' }, 'Restore from a downloaded backup'), restoreFile),
        ActionList(ActionRow('Restore configuration', 'Overwrite the current configuration with the selected backup after confirmation.', restoreButton)));

    const remoteCard = Card({
        title: 'Remote backup connection',
        subtitle: 'Configure and test the server used for backup integration.',
        footer: ActionList(
            ActionRow('Save connection', 'Store the remote server and feature settings on the device.', Button({
                onClick: async () => {
                    await api.put('/api/v1/updates/backup/config', {
                        enabled: remoteEnabled.get(), server_url: serverUrl.get() || null,
                        backup_enabled: backupEnabled.get(), restore_enabled: restoreEnabled.get(),
                    });
                    store.toast('Backup settings saved', 'success');
                }
            }, 'Save settings')),
            ActionRow('Test connection', 'Check whether the configured remote server responds.', Button({
                variant: 'outline', onClick: async () => {
                    const result = await api.get('/api/v1/updates/backup/check');
                    connectionStatus.textContent = result.connected ? 'Connected to the remote backup server.' : (result.reason || 'Remote backup server is not reachable.');
                }
            }, 'Test connection')),
        ),
    }, serverUrl.wrap, el('div', { class: 'toggle-stack' }, remoteEnabled.wrap, backupEnabled.wrap, restoreEnabled.wrap), connectionStatus);

    return el('div', {},
        el('div', { class: 'page-header' }, el('h1', {}, 'Backup & Restore'), el('p', {}, 'Protect, inspect, and restore the device configuration.')),
        remoteCard,
        card);
}

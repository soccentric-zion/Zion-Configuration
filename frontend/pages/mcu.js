// Companion MCU page: manifest-driven settings and signed firmware actions.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Field } from '../components/field.js';
import { Toggle } from '../components/toggle.js';
import { Button } from '../components/button.js';
import { EmptyState } from '../components/empty-state.js';
import { renderSchemaForm } from '../components/schema-form.js';
import { ActionList, ActionRow } from '../components/action-row.js';

export async function render(api, store) {
    const data = await api.get('/api/v1/io');
    const mcu = (data.manifest || []).find(peripheral => peripheral.kind === 'mcu');
    if (!mcu) {
        return el('div', {},
            el('div', { class: 'page-header' }, el('h1', {}, 'MCU'), el('p', {}, 'Companion microcontroller settings and firmware.')),
            EmptyState({ title: 'No companion MCU on this board' }));
    }

    const mcuData = await api.get('/api/v1/io/mcu').catch(() => ({ config: data.config && data.config.mcu }));
    const mcuCfg = mcuData.config || (data.config && data.config.mcu) || {};
    const enabled = Toggle({ label: 'Enable companion MCU', checked: mcuCfg.enabled !== false });
    const version = Field({ label: 'Firmware version' });
    const firmwareFile = el('input', { type: 'file', accept: '.bin,application/octet-stream', class: 'input' });
    const signatureFile = el('input', { type: 'file', accept: '.sig,application/octet-stream', class: 'input' });
    let schemaForm = null;
    let settings = el('p', { class: 'muted' }, 'No settings schema declared for this MCU.');
    const schemaId = mcu.caps && mcu.caps.settings_schema;
    if (schemaId) {
        const schemas = await api.get('/api/v1/schemas').catch(() => []);
        const descriptor = (schemas || []).find(item => item.id === schemaId);
        if (descriptor) {
            schemaForm = renderSchemaForm(descriptor.descriptor, mcuCfg.settings || {});
            settings = schemaForm.element;
        }
    }

    const firmwareCard = Card({
        title: mcu.human_name || 'Companion MCU',
        subtitle: `Current firmware: ${mcuCfg.firmware_version || 'unknown'} · ${mcu.device}`,
        footer: ActionList(ActionRow('Flash firmware', 'Upload the signed firmware image and its detached signature; the server verifies the signature against the board-manifest trust anchor before flashing.', Button({
            onClick: async () => {
                if (!version.get()) { store.toast('Enter a firmware version', 'error'); return; }
                const fw = firmwareFile.files && firmwareFile.files[0];
                const sig = signatureFile.files && signatureFile.files[0];
                if (!fw) { store.toast('Choose a firmware image', 'error'); return; }
                if (!sig) { store.toast('Choose a detached signature file', 'error'); return; }
                const sigBytes = new Uint8Array(await sig.arrayBuffer());
                const sigHex = Array.from(sigBytes).map(b => b.toString(16).padStart(2, '0')).join('');
                try {
                    const r = await api.upload(`/api/v1/io/mcu/firmware?version=${encodeURIComponent(version.get())}&signature=${sigHex}`, fw);
                    if (r.flashed) {
                        store.toast(`Firmware flashed (${r.bytes_written} bytes)`, 'success');
                    } else {
                        store.toast(`Verified and stored, not flashed: ${r.reason}`, 'info');
                    }
                } catch (e) { store.toast(e.message, 'error'); }
            },
        }, 'Verify + flash'))),
    }, el('div', { class: 'toggle-stack' }, enabled.wrap), settings, version.wrap,
        el('label', { class: 'field' }, el('span', { class: 'field-label' }, 'Firmware image (.bin)'), firmwareFile),
        el('label', { class: 'field' }, el('span', { class: 'field-label' }, 'Detached signature (.sig)'), signatureFile));

    const saveSettings = async () => {
        const config = data.config || {};
        await api.put('/api/v1/io', {
            serial: config.serial || [], can: config.can || [], gpio: config.gpio || [], cameras: config.cameras || [],
            mcu: { kind: mcuCfg.kind || 'stm32', enabled: enabled.get(), firmware_version: mcuCfg.firmware_version, settings: schemaForm ? schemaForm.getValue() : mcuCfg.settings || {} },
            board_leds: config.board_leds || {},
        });
        store.toast('MCU settings saved', 'success');
    };

    return el('div', {},
        el('div', { class: 'page-header' }, el('h1', {}, 'MCU'), el('p', {}, 'Configure the companion microcontroller and stage signed firmware.')),
        firmwareCard,
        Card({ title: 'MCU actions', subtitle: 'Persist settings separately from firmware staging.' }, ActionList(ActionRow('Save MCU settings', 'Persist the settings defined by the board manifest schema.', Button({ onClick: saveSettings }, 'Save settings')))));
}

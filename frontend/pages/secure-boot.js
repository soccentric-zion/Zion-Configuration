// Read-only secure boot and platform trust status.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Badge } from '../components/badge.js';

export async function render(api) {
    const trust = await api.get('/api/v1/security/trust');
    // `platform_status_available` distinguishes "the BSP reported these
    // values" from "no platform-status source exists on this board yet" —
    // never render the latter as if it were a completed, merely-empty read.
    const veritySection = trust.platform_status_available
        ? el('div', {}, el('div', { class: 'field-label' }, 'Verity and encryption'), el('div', { class: 'field-help' }, 'Read-only integrity and storage protection states.'), el('div', {}, `${trust.verity_status ?? 'unreported'} · ${trust.encryption_status ?? 'unreported'}`))
        : el('div', {}, el('div', { class: 'field-label' }, 'Verity and encryption'), el('div', { class: 'field-help' }, 'Read-only integrity and storage protection states.'), Badge('not available on this platform', 'neutral'));
    const signingKeySection = trust.platform_status_available
        && el('div', {}, el('div', { class: 'field-label' }, 'Update signing key'), el('div', { class: 'field-help' }, 'Key ID that signed the currently installed update.'), el('div', {}, trust.update_signing_key_id ?? 'unreported'));
    const trustBody = el('div', { class: 'field-row' },
        el('div', {}, el('div', { class: 'field-label' }, 'Secure boot'), el('div', { class: 'field-help' }, 'Firmware verification state reported by the platform.'), Badge(trust.secure_boot ? 'on' : 'off', trust.secure_boot ? 'success' : 'destructive')),
        el('div', {}, el('div', { class: 'field-label' }, 'TPM'), el('div', { class: 'field-help' }, 'Whether a trusted platform module is available.'), Badge(trust.tpm_present ? 'present' : 'absent', trust.tpm_present ? 'success' : 'neutral')),
        veritySection,
        signingKeySection);
    return el('div', {},
        el('div', { class: 'page-header' }, el('h1', {}, 'Secure Boot'), el('p', {}, 'Read-only platform integrity and trusted hardware status.')),
        Card({ title: 'Trust status', subtitle: 'Secure boot, TPM, verity, and encryption posture reported by the device.' }, trustBody));
}

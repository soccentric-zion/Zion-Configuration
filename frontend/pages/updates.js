// Local Updates page (spec §4.4): RAUC A/B slots and staged bundle install.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Field } from '../components/field.js';
import { Button } from '../components/button.js';
import { Table } from '../components/table.js';
import { StatusDot } from '../components/badge.js';
import { confirmDialog } from '../components/modal.js';
import { ActionList, ActionRow } from '../components/action-row.js';

export async function render(api, store) {
  const slots = await api.get('/api/v1/updates/slots');

  const slotRows = (slots || []).map(s => [
    s.slot, s.version,
    s.running ? el('span', {}, '●') : '',
    s.confirmed ? 'yes' : 'no',
    el('span', { class: 'row' }, StatusDot(s.state === 'good' ? 'ok' : s.state === 'bad' ? 'bad' : 'unknown'), s.state),
  ]);
  const slotsTable = Table({ columns: ['Slot', 'Version', 'Running', 'Confirmed', 'State'], caption: 'A/B slots' }, slotRows).element;

  // ---------- Install ----------
  const path = Field({ label: 'Bundle path (on device)', placeholder: '/data/updates/bundle.raucb' });
  const bundleFile = el('input', { type: 'file', accept: '.raucb,application/octet-stream', class: 'input' });
  const installCard = Card({
    title: 'Install bundle',
    subtitle: 'Compatibility (platform) + signature are verified before writing the inactive slot.',
    footer: ActionList(
      ActionRow('Reboot to apply', 'Activate the staged update on the next boot. The connection will briefly drop.', Button({
        variant: 'outline',
        onClick: async () => {
          if (!await confirmDialog({ title: 'Reboot to apply?', description: 'The staged slot becomes active on next boot.' })) return;
          await api.post('/api/v1/updates/reboot-to-apply', {});
          store.showReconnect();
        },
      }, 'Reboot to apply')),
      ActionRow('Upload bundle', 'Upload a signed bundle from this browser — verified and staged in one step.', Button({
        onClick: async () => {
          const file = bundleFile.files && bundleFile.files[0];
          if (!file) { store.toast('Choose a bundle file', 'error'); return; }
          try {
            const r = await api.upload('/api/v1/updates/upload', file);
            store.toast(`Staged: ${r.manifest && r.manifest.platform} ${r.manifest && r.manifest.version}. Reboot to apply.`, 'success');
          } catch (e) { store.toast(e.message, 'error'); }
        },
      }, 'Upload + install')),
      ActionRow('Install bundle', 'Verify and stage a bundle already staged on-device (e.g. via scp) by its path.', Button({
        variant: 'outline',
        onClick: async () => {
          if (!path.get()) { store.toast('Enter a bundle path', 'error'); return; }
          const r = await api.post('/api/v1/updates/install', { bundle_path: path.get() });
          store.toast(`Staged: ${r.manifest && r.manifest.platform} ${r.manifest && r.manifest.version}. Reboot to apply.`, 'success');
        },
      }, 'Install bundle'))),
  }, el('label', { class: 'field' }, el('span', { class: 'field-label' }, 'Bundle file (browser upload)'), bundleFile), path.wrap);

  // ---------- Confirm + Diagnostics (spec §4.4) ----------
  // A freshly booted image must be confirmed (health-check or manual) or the
  // bootloader rolls it back after N failed boots. The diagnostics bundle is a
  // tarball-ready snapshot: journal excerpt, redacted config, slots, dmesg,
  // network state, alert history, audit log.
  const confirmCard = Card({
    title: 'Confirm & diagnose',
    subtitle: 'Mark the booted image good (cancel rollback), or collect a diagnostics bundle.',
    footer: ActionList(
      ActionRow('Confirm booted image', 'Mark the running slot good so the bootloader does not roll it back after failed boots.', Button({
        variant: 'outline',
        onClick: async () => {
          if (!await confirmDialog({ title: 'Confirm booted image?', description: 'Marks the running slot good (RAUC mark-good). Rollback is cancelled.' })) return;
          await api.post('/api/v1/updates/confirm', {});
          store.toast('Booted image confirmed (mark-good).', 'success');
        },
      }, 'Confirm image')),
      ActionRow('Download diagnostics bundle', 'Collects journal excerpt, redacted config, slot status, dmesg, network state, alert history, and audit log.', Button({
        onClick: async () => {
          const d = await api.get('/api/v1/updates/diagnostics');
          const blob = new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `webconfig-diagnostics-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
          a.click();
          URL.revokeObjectURL(url);
          store.toast('Diagnostics bundle downloaded.', 'success');
        },
      }, 'Download diagnostics'))),
  });

  return el('div', {},
    el('div', { class: 'page-header' }, el('h1', {}, 'Updates'), el('p', {}, 'Manage local A/B slots and install signed bundles.')),
    Card({ title: 'A/B slots', subtitle: 'The active and inactive update slots, versions, and confirmation state.' }, slotsTable),
    installCard,
    confirmCard);
}

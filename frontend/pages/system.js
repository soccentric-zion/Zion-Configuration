// System settings page (spec §4.1): identity settings.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Field } from '../components/field.js';
import { Button } from '../components/button.js';
import { ActionList, ActionRow } from '../components/action-row.js';

export async function render(api, store) {
  const cfg = await api.get('/api/v1/system');

  const hostname = Field({ label: 'Hostname', value: cfg.hostname, placeholder: 'e.g. zion-01' });
  const assetTag = Field({ label: 'Asset tag', value: cfg.asset_tag });
  const hardwareRevision = Field({ label: 'Hardware revision', value: cfg.hardware_revision, help: 'The physical board revision assigned to this device.' });
  const serialNumber = Field({ label: 'Serial number', value: cfg.serial_number, help: 'The device serial number used for inventory and service records.' });
  const identity = el('div', { class: 'form-grid' }, hostname.wrap, assetTag.wrap, hardwareRevision.wrap, serialNumber.wrap);

  async function save() {
    const body = {
      hostname: hostname.get() || null,
      asset_tag: assetTag.get() || null,
      hardware_revision: hardwareRevision.get() || null,
      serial_number: serialNumber.get() || null,
      time: cfg.time,
      ssh: cfg.ssh,
      ntp_servers: cfg.ntp_servers || [],
    };
    await api.put('/api/v1/system', body);
    store.toast('System settings saved', 'success');
  }
  async function apply() {
    await api.post('/api/v1/system/apply', {});
    store.toast('Applied system settings', 'success');
  }

  const identityCard = Card({
    title: 'Identity',
    subtitle: 'Set the name and asset tag used to identify this device.',
    footer: ActionList(
      ActionRow('Apply to device', 'Apply the hostname settings to the running device.', Button({ variant: 'outline', onClick: apply }, 'Apply')),
      ActionRow('Save settings', 'Store identity settings for the next apply.', Button({ onClick: save }, 'Save'))),
  }, identity);

  return el('div', {},
    el('div', { class: 'page-header' }, el('h1', {}, 'System settings'), el('p', {}, 'Manage device identity and hostname settings.')),
    identityCard);
}

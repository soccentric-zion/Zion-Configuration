// Security page (spec §4.8): session/WireGuard config, TLS identity + CSR
// generation, certificate inventory (upload/delete) and exposure/trust
// (read-only).

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Field } from '../components/field.js';
import { Toggle } from '../components/toggle.js';
import { Button } from '../components/button.js';
import { Table } from '../components/table.js';
import { Badge } from '../components/badge.js';
import { EmptyState } from '../components/empty-state.js';
import { confirmDialog, formDialog, infoDialog } from '../components/modal.js';
import { ActionList, ActionRow } from '../components/action-row.js';

function downloadText(name, text) {
  const blob = new Blob([text], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = el('a', { href: url, download: name });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function render(api, store) {
  const [sec, exposure, wg, certs] = await Promise.all([
    api.get('/api/v1/security'),
    api.get('/api/v1/security/exposure'),
    api.get('/api/v1/security/wireguard/pubkey').catch(() => ({ public_key: '', has_private_key: false })),
    api.get('/api/v1/security/certs').catch(() => []),
  ]);

  // ---------- Session + WireGuard ----------
  const idle = Field({ label: 'Session idle timeout (s)', type: 'number', value: sec.session_idle_timeout_secs || 900 });
  const plainHttp = Toggle({ label: 'Allow plain HTTP (first-boot opt-in)', checked: sec.allow_plain_http });
  const wgEn = Toggle({ label: 'WireGuard enabled', checked: sec.wireguard && sec.wireguard.enabled });
  const wgPort = Field({ label: 'WireGuard port', type: 'number', value: sec.wireguard && sec.wireguard.port });

  const configCard = Card({
    title: 'Security config',
    subtitle: 'Session policy, plain HTTP access, and WireGuard settings.',
    footer: ActionList(
      ActionRow('Apply WireGuard', 'Apply the configured WireGuard interface and peers to the device.', Button({ variant: 'outline', onClick: async () => { await api.post('/api/v1/security/apply', {}); store.toast('WireGuard applied', 'success'); } }, 'Apply')),
      ActionRow('Save security settings', 'Store session, HTTP, WireGuard, and certificate settings.', Button({
        onClick: async () => {
          await api.put('/api/v1/security', {
            session_idle_timeout_secs: Number(idle.get()) || 0,
            allow_plain_http: plainHttp.get(),
            wireguard: { enabled: wgEn.get(), port: Number(wgPort.get()) || 0, peers: (sec.wireguard && sec.wireguard.peers) || [] },
            certificates: sec.certificates || [],
          });
          store.toast('Security config saved', 'success');
        },
      }, 'Save'))),
  }, idle.wrap, plainHttp.wrap, el('div', { class: 'toggle-stack' }, wgEn.wrap), wgPort.wrap,
    el('div', { class: 'row' }, el('span', { class: 'muted text-sm' }, 'WireGuard public key:'),
      wg.public_key ? el('code', { class: 'mono' }, wg.public_key) : Badge(wg.has_private_key ? 'key present, no peer' : 'not generated yet', 'neutral')));

  // ---------- TLS identity: customer cert/key upload + CSR generator ----------
  const tlsCert = Field({ label: 'Certificate (PEM)', type: 'textarea', placeholder: '-----BEGIN CERTIFICATE-----\n…', help: 'Leaf cert first, then any intermediates. Replaces the self-signed identity.' });
  const tlsKey = Field({ label: 'Private key (PEM)', type: 'textarea', placeholder: '-----BEGIN PRIVATE KEY-----\n…', help: 'Must match the leaf cert above. Stored mode 0600.' });
  const tlsCard = Card({
    title: 'TLS identity',
    subtitle: 'Replace the per-device self-signed cert with a customer-provided cert/key pair.',
    footer: ActionList(ActionRow('Upload cert/key', 'Validate the pair and atomically replace the device TLS identity. The running listener keeps the old cert until the daemon restarts.', Button({
      onClick: async () => {
        if (!tlsCert.get().trim() || !tlsKey.get().trim()) { store.toast('Paste both the cert and the key', 'error'); return; }
        try {
          await api.post('/api/v1/security/tls', { cert: tlsCert.get(), key: tlsKey.get() });
          tlsCert.set(''); tlsKey.set('');
          await infoDialog({ title: 'Identity updated', body: el('div', { class: 'stack text-sm' }, el('p', {}, 'The cert/key pair was validated and written. Restart the WebConfig daemon (or reboot) for it to take effect — the running listener still serves the previous cert.'), el('p', { class: 'muted' }, 'A mismatched pair is rejected before anything is written, so a bad upload cannot break TLS on the next boot.')) });
          store.toast('TLS identity updated', 'success');
        } catch (e) { store.toast(e.message, 'error'); }
      },
    }, 'Upload'))),
  }, tlsCert.wrap, tlsKey.wrap);

  const cn = Field({ label: 'Common name', placeholder: 'device.example.com' });
  const csrOutput = el('div', { hidden: true });
  const csrCard = Card({ title: 'Generate CSR', subtitle: 'For a customer CA to sign — no OpenSSL involved (rustls/rcgen).' },
    cn.wrap,
    ActionList(ActionRow('Generate CSR', 'Create a certificate signing request and private key for the supplied common name.', Button({
      onClick: async () => {
        if (!cn.get()) { store.toast('Enter a common name', 'error'); return; }
        const r = await api.post('/api/v1/security/csr', { cn: cn.get() });
        csrOutput.hidden = false;
        csrOutput.replaceChildren(
          el('div', { class: 'field-help' }, 'Send the CSR to your CA; keep the private key on this device.'),
          el('div', { class: 'row' },
            Button({ variant: 'outline', size: 'sm', title: 'Download the certificate signing request', onClick: () => downloadText('device.csr', r.csr) }, 'Download CSR'),
            Button({ variant: 'outline', size: 'sm', title: 'Download the private key and keep it protected', onClick: () => downloadText('device.key', r.private_key) }, 'Download private key')));
      },
    }, 'Generate'))),
    csrOutput);

  // ---------- Certificate store: inventory + upload/delete ----------
  const certRows = (certs || []).map(c => [
    c.id, Badge(c.kind, 'neutral'), c.subject, c.not_after,
    Button({ variant: 'outline', size: 'sm', title: 'Remove this certificate from the device store', onClick: async () => {
      if (await confirmDialog({ title: `Remove ${c.id}?`, description: 'Deletes the PEM file and its metadata from the device.', confirmLabel: 'Remove', variant: 'destructive' })) {
        try { await api.del(`/api/v1/security/certs/${encodeURIComponent(c.id)}`); store.toast(`${c.id} removed`, 'success'); store.reload(); }
        catch (e) { store.toast(e.message, 'error'); }
      }
    } }, 'Remove'),
  ]);
  const certsCard = Card({
    title: 'Certificates',
    subtitle: 'CA + client certs for MQTT/VPN, stored on the device and referenced elsewhere. Expiry feeds the alert engine.',
    footer: ActionList(ActionRow('Upload certificate', 'Add a CA or client cert to the device store. The PEM is validated before it is saved.', Button({
      onClick: async () => {
        const v = await formDialog({
          title: 'Upload certificate',
          confirmLabel: 'Upload',
          fields: [
            { key: 'id', label: 'ID', placeholder: 'ca-root', help: 'Alphanumeric, dash, underscore only.', required: true },
            { key: 'kind', label: 'Kind', type: 'select', options: [{ value: 'ca', label: 'CA' }, { value: 'client', label: 'Client' }] },
            { key: 'subject', label: 'Subject', placeholder: 'CN=ca.example', required: true },
            { key: 'not_after', label: 'Expires', placeholder: '2026-08-23', help: 'ISO-8601 date — feeds the cert-expiry alert.', required: true },
            { key: 'pem', label: 'PEM', type: 'textarea', placeholder: '-----BEGIN CERTIFICATE-----\n…', required: true },
          ],
        });
        if (!v) return;
        if (!v.id || !v.subject || !v.pem || !v.not_after) { store.toast('Fill in every field', 'error'); return; }
        try { await api.post('/api/v1/security/certs', v); store.toast(`${v.id} uploaded`, 'success'); store.reload(); }
        catch (e) { store.toast(e.message, 'error'); }
      },
    }, 'Upload'))),
  }, certRows.length ? Table({ columns: ['ID', 'Kind', 'Subject', 'Expires', ''], caption: 'Certificates' }, certRows).element : EmptyState({ title: 'No certificates on file' }));

  // ---------- Exposure (read-only) ----------
  const exposureCard = Card({ title: 'Exposure', subtitle: 'Effective UI reachability and open services by interface.' },
    el('div', { class: 'field-row' },
      el('div', {}, el('div', { class: 'field-label' }, 'UI reachability'), el('div', { class: 'field-help' }, 'The WebConfig interface remains reachable for local recovery.'), Badge(exposure.ui_always_reachable ? 'yes' : 'no', exposure.ui_always_reachable ? 'info' : 'neutral')),
      el('div', {}, el('div', { class: 'field-label' }, 'Plain HTTP'), el('div', { class: 'field-help' }, 'Whether unencrypted HTTP access is permitted.'), Badge(exposure.plain_http ? 'allowed' : 'blocked', exposure.plain_http ? 'warning' : 'success'))),
    el('div', { class: 'stack' }, ...(exposure.per_interface || []).map(p =>
      el('div', { class: 'row text-sm' }, el('strong', {}, p.interface + ':'), (p.open_services || []).join(', ') || 'none'))));

  return el('div', {},
    el('div', { class: 'page-header' }, el('h1', {}, 'Security'), el('p', {}, 'Sessions, WireGuard, TLS, and exposure/trust posture.')),
    configCard,
    el('div', { class: 'card-grid' }, tlsCard, csrCard, certsCard, exposureCard));
}
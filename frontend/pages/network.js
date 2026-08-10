// Networking page (spec §4.2 flagship): interface editor + commit/confirm
// rollback. Applying is provisional — the caller must confirm within the
// window or the engine bridge auto-rolls-back (see core/store.js).

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Field } from '../components/field.js';
import { Toggle } from '../components/toggle.js';
import { Button } from '../components/button.js';
import { ActionList, ActionRow } from '../components/action-row.js';
import { EmptyState } from '../components/empty-state.js';

function isWirelessInterface(name) {
  return /^(wl|wlan|wwan|rmnet|ccmni)/i.test(name);
}

export async function renderNetworkPage(api, store, mode = 'all') {
  const { config, live } = await api.get('/api/v1/network');

  const liveNames = (live || []).map(l => l.name);
  const ifaces = (config.interfaces || []).slice();
  const wifiNetworks = (config.wifi || []).map(network => ({ ...network }));
  for (const n of liveNames) {
    if (!ifaces.some(i => i.name === n)) ifaces.push({ name: n, enabled: true, dhcp: true });
  }

  const wiredHost = el('div', { class: 'card-grid' });
  const wirelessIfaceHost = el('div', { class: 'card-grid' });
  function renderIfaces(host, interfaceList) {
    host.replaceChildren(...interfaceList.map((iface) => {
      const en = Toggle({ label: 'Enabled', checked: iface.enabled });
      const dhcp = Toggle({ label: 'DHCP', checked: iface.dhcp });
      const mac = Field({ label: 'MAC address override', value: iface.mac_address, placeholder: 'AA:BB:CC:DD:EE:FF', help: 'Optional hardware address assigned when this interface is brought up.' });
      const addr = Field({ label: 'Static address', value: iface.address, placeholder: '192.168.1.50' });
      const prefix = Field({ label: 'Prefix', type: 'number', value: iface.prefix });
      const gw = Field({ label: 'Gateway', value: iface.gateway });
      const sync = () => {
        const index = ifaces.findIndex(item => item.name === iface.name);
        if (index < 0) return;
        ifaces[index] = {
          name: iface.name, mac_address: mac.get() || null, enabled: en.get(), dhcp: dhcp.get(),
          address: addr.get() || null, prefix: prefix.get() ? Number(prefix.get()) : null,
          gateway: gw.get() || null, mtu: iface.mtu, dns: iface.dns || [],
          open_services: iface.open_services || [],
        };
      };
      for (const f of [en.input, dhcp.input, mac.input, addr.input, prefix.input, gw.input]) {
        f.addEventListener('change', sync);
      }
      return Card({ title: iface.name, subtitle: 'Interface address, DHCP, gateway, and exposed services.', class: 'iface-card' },
        el('div', { class: 'toggle-stack' }, en.wrap, dhcp.wrap), mac.wrap, addr.wrap, prefix.wrap, gw.wrap);
    }));
  }
  renderIfaces(wiredHost, ifaces.filter(iface => !isWirelessInterface(iface.name)));
  renderIfaces(wirelessIfaceHost, ifaces.filter(iface => isWirelessInterface(iface.name)));

  const wifiSsid = Field({ label: 'Network name (SSID)', placeholder: 'Office Wi-Fi' });
  const wifiPsk = Field({ label: 'Password', type: 'password', placeholder: 'Network password' });
  const wifiPriority = Field({ label: 'Priority', type: 'number', value: 0, help: 'Higher-priority networks are preferred when several are available.' });
  const wifiHidden = Toggle({ label: 'Hidden network' });
  const fiveG = config.cellular_5g || {};
  const fiveGEnabled = Toggle({ label: 'Enable 5G mobile broadband', checked: fiveG.enabled === true });
  const fiveGAutoConnect = Toggle({ label: 'Connect automatically', checked: fiveG.auto_connect !== false });
  const fiveGRoaming = Toggle({ label: 'Allow roaming', checked: fiveG.roaming === true });
  const fiveGModem = Field({ label: 'Modem device', value: fiveG.modem, placeholder: '/dev/cdc-wdm0' });
  const fiveGApn = Field({ label: 'APN', value: fiveG.apn, placeholder: 'internet' });
  const fiveGPin = Field({ label: 'SIM PIN', type: 'password', value: fiveG.pin, placeholder: 'Optional' });
  const fiveGUsername = Field({ label: 'Username', value: fiveG.username });
  const fiveGPassword = Field({ label: 'Password', type: 'password', value: fiveG.password });
  const scanResults = el('div', { class: 'stack' });
  const savedNetworks = el('div', { class: 'stack' });

  function renderSavedNetworks() {
    if (!wifiNetworks.length) {
      savedNetworks.replaceChildren(EmptyState({ title: 'No wireless networks saved' }));
      return;
    }
    savedNetworks.replaceChildren(...wifiNetworks.map((network, index) => el('div', { class: 'action-row' },
      el('div', { class: 'action-row-copy' },
        el('h3', {}, network.ssid),
        el('p', {}, `${network.hidden ? 'Hidden' : 'Visible'} network · priority ${network.priority || 0}`)),
      Button({ variant: 'destructive-outline', size: 'sm', onClick: () => { wifiNetworks.splice(index, 1); renderSavedNetworks(); } }, 'Remove'))));
  }

  function renderScanResults(results) {
    if (!results.length) {
      scanResults.replaceChildren(el('p', { class: 'muted' }, 'No wireless networks found.'));
      return;
    }
    scanResults.replaceChildren(...results.filter(result => result.ssid).map(result => ActionRow(
      result.ssid,
      `${result.signal_dbm} dBm`,
      Button({ variant: 'outline', size: 'sm', onClick: () => wifiSsid.set(result.ssid) }, 'Use network'))));
  }

  renderSavedNetworks();
  const wirelessCard = Card({
    title: 'Wireless networks',
    subtitle: 'Add saved Wi-Fi networks and choose one from a live scan.',
    footer: ActionList(
      ActionRow('Add wireless network', 'Save the SSID and connection details for the next network apply.', Button({
        onClick: () => {
          const ssid = wifiSsid.get().trim();
          if (!ssid) { store.toast('Enter a network name', 'error'); return; }
          wifiNetworks.push({ ssid, psk: wifiPsk.get() || null, priority: Number(wifiPriority.get()) || 0, hidden: wifiHidden.get() });
          wifiSsid.set(''); wifiPsk.set(''); wifiPriority.set('0'); wifiHidden.set(false); renderSavedNetworks();
        },
      }, 'Add network')),
      ActionRow('Scan for networks', 'Look for nearby Wi-Fi networks and use one to fill the SSID.', Button({
        variant: 'outline',
        onClick: async () => {
          scanResults.replaceChildren(el('p', { class: 'muted' }, 'Scanning…'));
          try { renderScanResults(await api.get('/api/v1/network/scan')); } catch (error) { scanResults.replaceChildren(el('p', { class: 'error-text' }, error.message || 'Wireless scan failed')); }
        },
      }, 'Scan'))),
  },
    el('div', { class: 'form-grid' }, wifiSsid.wrap, wifiPsk.wrap, wifiPriority.wrap, wifiHidden.wrap),
    el('div', {}, el('h3', {}, 'Saved networks'), savedNetworks),
    el('div', {}, el('h3', {}, 'Nearby networks'), scanResults));

  const fiveGCard = Card({
    title: '5G mobile broadband',
    subtitle: 'Not implemented in this release — settings are saved but have no effect until a ModemManager-backed applier ships (deferred to v2 per spec §4.2).',
  },
    fiveGEnabled.wrap,
    el('div', { class: 'form-grid' }, fiveGModem.wrap, fiveGApn.wrap, fiveGPin.wrap, fiveGUsername.wrap, fiveGPassword.wrap),
    el('div', { class: 'toggle-stack' }, fiveGAutoConnect.wrap, fiveGRoaming.wrap));

  const dns = Field({ label: 'DNS servers (comma)', value: (config.dns_servers || []).join(', ') });
  const confirmWindow = Field({ label: 'Confirm window (s)', type: 'number', value: config.confirm_window_secs || 90 });
  const apEn = Toggle({ label: 'AP provisioning mode', checked: config.ap_mode && config.ap_mode.enabled });
  const apTrigger = Field({
    label: 'Provisioning trigger', type: 'select',
    value: (config.ap_mode && config.ap_mode.trigger) || 'no-config',
    help: 'no-config enters AP mode automatically when no wired/network config is present; gpio-button waits for the board provision button.',
    options: [{ value: 'no-config', label: 'No config detected' }, { value: 'gpio-button', label: 'GPIO provision button' }],
  });
  const apSsid = Field({ label: 'AP SSID', value: config.ap_mode && config.ap_mode.ssid });
  const apPass = Field({ label: 'AP passphrase', type: 'password', value: config.ap_mode && config.ap_mode.passphrase });

  async function apply() {
    const body = {
      interfaces: ifaces,
      wifi: wifiNetworks,
      regulatory_domain: config.regulatory_domain || null,
      dns_servers: dns.get().split(',').map(s => s.trim()).filter(Boolean),
      firewall: config.firewall || { default_open: [] },
      confirm_window_secs: Number(confirmWindow.get()) || 90,
      ap_mode: {
        enabled: apEn.get(),
        trigger: apTrigger.get() || 'no-config',
        ssid: apSsid.get(), passphrase: apPass.get(),
      },
      vlan: config.vlan || [], bridges: config.bridges || [], cellular: config.cellular || [],
      cellular_5g: {
        enabled: fiveGEnabled.get(), modem: fiveGModem.get() || null, apn: fiveGApn.get() || null,
        pin: fiveGPin.get() || null, username: fiveGUsername.get() || null, password: fiveGPassword.get() || null,
        roaming: fiveGRoaming.get(), auto_connect: fiveGAutoConnect.get(),
      },
    };
    await api.put('/api/v1/network', body);
    const res = await api.post('/api/v1/network/apply', {});
    if (res && res.provisional) store.showConfirm(res.confirm_window_secs || Number(confirmWindow.get()) || 90);
    else store.toast('Network applied', 'success');
  }
  async function rollback() {
    await api.post('/api/v1/network/rollback', {});
    store.hideConfirm();
    store.toast('Rolled back');
  }

  // Spec §4.2 connectivity test: gateway → public IP → DNS resolve, each
  // reported as {ok, detail}. Surfaces the live reachability of the configured
  // network without the operator needing shell access.
  const connResult = el('div', { class: 'stack' });
  function renderConnResult(r) {
    const row = (label, layer) => el('div', { class: 'action-row' },
      el('div', { class: 'action-row-copy' },
        el('h3', {}, label),
        el('p', { class: layer.ok ? 'muted' : 'error-text' }, layer.detail || (layer.ok ? 'ok' : 'failed'))),
      el('span', { class: layer.ok ? 'badge badge-success' : 'badge badge-destructive' }, layer.ok ? 'Reachable' : 'Failed'));
    connResult.replaceChildren(
      row('Gateway', r.gateway || { ok: false, detail: '—' }),
      row('Public internet', r.public || { ok: false, detail: '—' }),
      row('DNS resolution', r.dns || { ok: false, detail: '—' }));
  }
  async function runConnectivity() {
    connResult.replaceChildren(el('p', { class: 'muted' }, 'Testing…'));
    try { renderConnResult(await api.post('/api/v1/network/connectivity-test', {})); }
    catch (error) { connResult.replaceChildren(el('p', { class: 'error-text' }, error.message || 'Connectivity test failed')); }
  }

  const showWired = mode === 'all' || mode === 'wired';
  const showWireless = mode === 'all' || mode === 'wireless';
  const showFiveG = mode === 'all' || mode === '5g';
  const title = mode === 'wired' ? 'Wired networking' : mode === 'wireless' ? 'Wireless networking' : mode === '5g' ? '5G networking' : 'Networking';
  const content = [
    showWired ? el('section', { class: 'network-wired-section' },
      el('h2', {}, 'Wired'),
      el('p', { class: 'muted' }, `${ifaces.filter(iface => !isWirelessInterface(iface.name)).length} interfaces detected.`),
      wiredHost) : null,
    showWireless ? el('section', { class: 'network-wireless-section' },
      el('h2', {}, 'Wireless'),
      el('p', { class: 'muted' }, 'Wi-Fi connections and wireless interfaces.'),
      wirelessIfaceHost, wirelessCard) : null,
    showFiveG ? fiveGCard : null,
    showWireless ? Card({ title: 'DNS + AP mode', subtitle: 'Set name servers and the fallback access-point provisioning behavior.' }, dns.wrap, confirmWindow.wrap, el('div', { class: 'toggle-stack' }, apEn.wrap), apTrigger.wrap, apSsid.wrap, apPass.wrap) : null,
    Card({ title: 'Network actions', subtitle: 'Applying network settings is provisional until the new connection is confirmed.' },
      ActionList(
        ActionRow('Save and apply', 'Write the edited settings and apply them. The connection may move to a new address.', Button({ onClick: apply }, 'Apply settings')),
        ActionRow('Roll back now', 'Restore the previous network configuration immediately.', Button({ variant: 'outline', onClick: rollback }, 'Roll back')),
        ActionRow('Test connectivity', 'Ping the configured gateway, a public IP, and resolve a name via the configured DNS.', Button({ variant: 'outline', onClick: runConnectivity }, 'Run test'))),
      connResult),
  ];

  return el('div', {},
    el('div', { class: 'page-header' }, el('h1', {}, title),
      el('p', {}, 'Applying is provisional — reconnect on the new address and confirm within the window, or the change rolls back automatically.')),
    ...content.filter(Boolean));
}

export async function render(api, store) {
  return renderNetworkPage(api, store);
}

// Firewall and exposed service policy.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Field } from '../components/field.js';
import { Toggle } from '../components/toggle.js';
import { Button } from '../components/button.js';
import { ActionList, ActionRow } from '../components/action-row.js';

export async function render(api, store) {
    const { config } = await api.get('/api/v1/network');
    const enabled = Toggle({ label: 'Enable firewall', checked: config.firewall ? config.firewall.enabled !== false : true });
    const services = Field({ label: 'Default-open services (comma-separated)', value: ((config.firewall && config.firewall.default_open) || []).join(', '), help: 'Services opened by default on supported interfaces.' });
    const openPorts = Field({
        label: 'Default-open ports',
        value: formatPorts((config.firewall && config.firewall.default_open_ports) || []),
        placeholder: '9000/tcp, 5353/udp',
        help: 'Additional ports opened on every enabled interface. Use port/protocol pairs.',
    });
    const ifaceFields = (config.interfaces || []).map(iface => {
        const servicesField = Field({ label: `${iface.name} services`, value: (iface.open_services || []).join(', '), help: 'Known services exposed on this interface.' });
        const portsField = Field({ label: `${iface.name} ports`, value: formatPorts(iface.open_ports || []), placeholder: '9000/tcp, 5353/udp', help: 'Additional TCP/UDP ports exposed on this interface.' });
        return { services: servicesField, ports: portsField };
    });

    async function save() {
        const interfaces = (config.interfaces || []).map((iface, index) => ({
            ...iface,
            open_services: splitValues(ifaceFields[index].services.get()),
            open_ports: parsePorts(ifaceFields[index].ports.get()),
        }));
        await api.put('/api/v1/network', {
            ...config,
            interfaces,
            firewall: {
                ...(config.firewall || {}),
                enabled: enabled.get(),
                default_open: splitValues(services.get()),
                default_open_ports: parsePorts(openPorts.get()),
            },
        });
        const result = await api.post('/api/v1/network/apply', {});
        if (result && result.provisional) store.showConfirm(result.confirm_window_secs || config.confirm_window_secs || 90);
        else store.toast('Firewall settings applied', 'success');
    }

    return el('div', {},
        el('div', { class: 'page-header' }, el('h1', {}, 'Firewall'), el('p', {}, 'Control which services are exposed by default and on each network interface.')),
        Card({ title: 'Service and port policy', subtitle: 'Changes are applied through the network confirmation workflow.', footer: ActionList(ActionRow('Save and apply', 'Write the firewall policy and apply it to the device.', Button({ onClick: save }, 'Apply settings'))) }, enabled.wrap, services.wrap, openPorts.wrap, ...ifaceFields.flatMap(fields => [fields.services.wrap, fields.ports.wrap])));
}

function splitValues(value) {
    return value.split(',').map(item => item.trim()).filter(Boolean);
}

function parsePorts(value) {
    return splitValues(value).map(item => {
        const [port, protocol = 'tcp'] = item.toLowerCase().split('/').map(part => part.trim());
        return { port: Number(port), protocol };
    });
}

function formatPorts(ports) {
    return ports.map(item => `${item.port}/${item.protocol}`).join(', ');
}

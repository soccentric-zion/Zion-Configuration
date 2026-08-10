// GPS/GNSS time source configuration.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Field } from '../components/field.js';
import { Toggle } from '../components/toggle.js';
import { Button } from '../components/button.js';
import { ActionList, ActionRow } from '../components/action-row.js';

export async function render(api, store) {
    const cfg = await api.get('/api/v1/system');
    const enabled = Toggle({ label: 'GPS/GNSS time enabled', checked: cfg.time && cfg.time.gps_enabled });
    const device = Field({
        label: 'GPS/GNSS device',
        value: cfg.time && cfg.time.gps_device,
        placeholder: '/dev/ttyUSB0',
        help: 'Serial device used by the receiver or gpsd.',
    });
    const ppsDevice = Field({
        label: 'GPS PPS device (optional)',
        value: cfg.time && cfg.time.gps_pps_device,
        placeholder: '/dev/pps0',
        help: 'Kernel PPS device for sub-second synchronization.',
    });

    async function save() {
        await api.put('/api/v1/system', {
            ...cfg,
            time: {
                ...cfg.time,
                gps_enabled: enabled.get(),
                gps_device: device.get() || null,
                gps_pps_device: ppsDevice.get() || null,
            },
        });
        store.toast('GPS/GNSS settings saved', 'success');
    }

    async function apply() {
        await api.post('/api/v1/system/apply', {});
        store.toast('GPS/GNSS settings applied', 'success');
    }

    return el('div', { class: 'time-page' },
        el('div', { class: 'page-header' },
            el('h1', {}, 'GPS / GNSS'),
            el('p', {}, 'Use a satellite receiver as the device time source.')),
        Card({
            title: 'GPS/GNSS synchronization',
            subtitle: 'Enable PPS when the receiver and kernel expose a precision pulse.',
            footer: ActionList(
                ActionRow('Apply to device', 'Apply clock synchronization settings now.', Button({ variant: 'outline', onClick: apply }, 'Apply')),
                ActionRow('Save settings', 'Store the receiver and PPS device paths.', Button({ onClick: save }, 'Save')),
            ),
        }, el('div', { class: 'form-grid' }, enabled.wrap, device.wrap, ppsDevice.wrap)),
    );
}

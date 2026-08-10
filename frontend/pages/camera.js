// Camera page: board-manifest camera inventory and platform capabilities.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Toggle } from '../components/toggle.js';
import { Button } from '../components/button.js';
import { ActionList, ActionRow } from '../components/action-row.js';
import { EmptyState } from '../components/empty-state.js';
import { peripheralsOf, saveIo } from './hardware-utils.js';

export async function render(api, store) {
    const data = await api.get('/api/v1/io');
    const cameras = peripheralsOf(data, 'camera');
    const panel = await api.get('/api/v1/io/platform-panel').catch(() => ({}));
    const subsystemEnabled = Toggle({ label: 'Enable camera subsystem', checked: data.config && data.config.cameras_enabled !== false });
    const cameraStates = cameras.map(camera => {
        const existing = (data.config && data.config.cameras || []).find(item => item.name === camera.id) || {};
        return { camera, enabled: Toggle({ label: 'Enable camera', checked: existing.enabled !== false }) };
    });

    const save = async () => {
        await saveIo(api, data, { cameras_enabled: subsystemEnabled.get(), cameras: cameraStates.map(({ camera, enabled }) => ({ name: camera.id, enabled: enabled.get() })) });
        store.toast('Camera settings saved', 'success');
    };

    const cameraCards = cameraStates.map(({ camera, enabled }) => Card({
        title: camera.human_name || camera.id,
        subtitle: camera.device,
    },
        enabled.wrap,
        el('div', { class: 'row' }, el('span', {}, 'Capabilities'), el('span', { class: 'muted' }, Object.keys(camera.caps || {}).length ? Object.entries(camera.caps).map(([key, value]) => `${key}: ${value}`).join(', ') : 'Board-defined defaults'))));

    const panelCameras = panel.cameras || [];
    const capabilityCard = panelCameras.length ? Card({ title: 'Platform camera inventory', subtitle: 'Additional board-level camera information reported by the platform panel.' },
        ...panelCameras.map(camera => el('div', { class: 'row' }, el('strong', {}, camera.human_name || camera.id), el('span', { class: 'muted' }, camera.device)))) : null;

    return el('div', {},
        el('div', { class: 'page-header' }, el('h1', {}, 'Camera'), el('p', {}, 'Inspect camera devices defined by the board manifest.')),
        Card({ title: 'Camera subsystem', subtitle: 'Enable cameras before configuring individual devices.' }, subsystemEnabled.wrap),
        cameras.length ? el('div', { class: 'card-grid' }, ...cameraCards) : EmptyState({ title: 'No cameras on this board' }),
        capabilityCard,
        cameras.length ? Card({ title: 'Camera actions' }, ActionList(ActionRow('Save camera settings', 'Persist which manifest cameras are enabled for the device.', Button({ onClick: save }, 'Save settings')))) : null);
}

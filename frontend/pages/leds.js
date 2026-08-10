// Board LED control through manifest-declared GPIO outputs.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Field } from '../components/field.js';
import { Toggle } from '../components/toggle.js';
import { Button } from '../components/button.js';
import { Badge } from '../components/badge.js';
import { ActionList, ActionRow } from '../components/action-row.js';
import { EmptyState } from '../components/empty-state.js';
import { peripheralsOf, saveIo } from './hardware-utils.js';

function isLed(peripheral) {
    const text = `${peripheral.id} ${peripheral.human_name}`.toLowerCase();
    return peripheral.kind === 'gpio' && (text.includes('led') || peripheral.caps?.function === 'led');
}

export async function render(api, store) {
    const data = await api.get('/api/v1/io');
    const services = await api.get('/api/v1/services').catch(() => []);
    const leds = peripheralsOf(data, 'gpio').filter(isLed);
    const boardLeds = data.config && data.config.board_leds || {};
    const appConfig = boardLeds.app || {};
    const ledOptions = [{ value: '', label: 'Not assigned' }, ...leds.map(led => ({ value: led.peripheral ? led.peripheral.id : led.id, label: led.human_name || led.id }))];
    const serviceOptions = [{ value: '', label: 'Any application' }, ...(services || []).map(service => ({ value: service.unit, label: service.unit }))];
    const boardLedsEnabled = Toggle({ label: 'Enable automatic board status LEDs', checked: boardLeds.enabled !== false });
    const systemLed = Field({ label: 'System status LED', type: 'select', value: boardLeds.system_led || '', options: ledOptions, help: 'Red indicates a system error, green indicates good, and amber indicates a warning.' });
    const osLed = Field({ label: 'OS status LED', type: 'select', value: boardLeds.os_led || '', options: ledOptions, help: 'Used for operating-system health and service readiness.' });
    const hardwareLed = Field({ label: 'Hardware status LED', type: 'select', value: boardLeds.hardware_led || '', options: ledOptions, help: 'Used for thermal, peripheral, and hardware faults.' });
    const appLedsEnabled = Toggle({ label: 'Enable application status LED', checked: appConfig.enabled });
    const appLed = Field({ label: 'Application LED', type: 'select', value: appConfig.led || '', options: ledOptions });
    const appService = Field({ label: 'Application service', type: 'select', value: appConfig.service || '', options: serviceOptions, help: 'The selected service drives the application LED status.' });
    const rows = leds.map(peripheral => {
        const existing = (data.config && data.config.gpio || []).find(item => item.name === peripheral.id) || {};
        return { peripheral, enabled: Toggle({ label: 'Enable LED', checked: existing.enabled !== false }), initial: Toggle({ label: 'On at boot', checked: existing.initial_state === true }), existing };
    });
    const save = async () => {
        await saveIo(api, data, {
            gpio: [...(data.config && data.config.gpio || []).filter(item => !rows.some(row => row.peripheral.id === item.name)), ...rows.map(row => ({ name: row.peripheral.id, enabled: row.enabled.get(), direction: 'out', active_low: row.peripheral.caps?.active_low || false, initial_state: row.initial.get() }))],
            board_leds: {
                enabled: boardLedsEnabled.get(),
                system_led: systemLed.get() || null,
                os_led: osLed.get() || null,
                hardware_led: hardwareLed.get() || null,
                app: { enabled: appLedsEnabled.get(), led: appLed.get() || null, service: appService.get() || null },
            },
        });
        store.toast('Board LED settings saved', 'success');
    };

    const policyCard = Card({
        title: 'Status LED policy',
        subtitle: 'Assign one LED to each device status domain. The policy uses red for errors, green for good, and amber for warnings.',
    }, boardLedsEnabled.wrap, el('div', { class: 'form-grid' }, systemLed.wrap, osLed.wrap, hardwareLed.wrap));
    const appCard = Card({
        title: 'Application status',
        subtitle: 'Optionally dedicate one LED to a selected application service.',
    }, appLedsEnabled.wrap, el('div', { class: 'form-grid' }, appLed.wrap, appService.wrap));
    const legendCard = Card({ title: 'Status meanings', subtitle: 'The automatic policy uses the same meanings across system, OS, hardware, and application indicators.' },
        el('div', { class: 'row' }, Badge('good', 'success'), el('span', {}, 'Green: operating normally')),
        el('div', { class: 'row' }, Badge('warning', 'warning'), el('span', {}, 'Amber: warning or degraded state')),
        el('div', { class: 'row' }, Badge('error', 'destructive'), el('span', {}, 'Red: error or fault')));

    return el('div', {},
        el('div', { class: 'page-header' }, el('h1', {}, 'Board LEDs'), el('p', {}, 'Control status LEDs declared as GPIO outputs by the board manifest.')),
        policyCard,
        appCard,
        legendCard,
        rows.length ? rows.map(row => Card({ title: row.peripheral.human_name || row.peripheral.id, subtitle: row.peripheral.device }, el('div', { class: 'toggle-stack' }, row.enabled.wrap, row.initial.wrap), Button({ variant: 'outline', size: 'sm', onClick: async () => { const result = await api.post(`/api/v1/io/gpio/${encodeURIComponent(row.peripheral.id)}/toggle`, {}); store.toast(`${row.peripheral.id} → ${result.value}`, 'success'); } }, 'Toggle now'))) : EmptyState({ title: 'No board LEDs reported by this board' }),
        rows.length ? Card({ title: 'LED actions' }, ActionList(ActionRow('Save settings', 'Persist the startup state for each board LED.', Button({ onClick: save }, 'Save settings')))) : null);
}

// Base clock settings page: timezone and manual time for air-gapped systems.
// NTP, PTP, and GPS/GNSS have dedicated pages; this page owns the timezone and
// manual datetime used for air-gapped systems.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Field } from '../components/field.js';
import { Button } from '../components/button.js';
import { ActionList, ActionRow } from '../components/action-row.js';

// Full IANA tzdata catalog, sourced at runtime from the browser's own tz
// database via Intl.supportedValuesOf (ECMA-402, all evergreen browsers) —
// no bundler, no build step, no hardcoded/stale/US-only list to maintain.
function allTimezones() {
    try {
        return Intl.supportedValuesOf('timeZone');
    } catch (_) {
        // Only reachable on a browser old enough to lack Intl.supportedValuesOf
        // (added ~2022) — a minimal fallback keeps the page usable rather than
        // throwing.
        return ['UTC', 'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles'];
    }
}

export async function render(api, store) {
    const cfg = await api.get('/api/v1/system');
    // Spec §4.1: a board without a battery-backed RTC loses the wall clock on
    // every power loss. Surface that so the operator knows NTP/GPS is required
    // (or a manual datetime must be re-entered after each boot).
    let rtcBanner = null;
    try {
        const manifest = await api.get('/api/v1/manifest');
        if (manifest && manifest.has_rtc === false) {
            rtcBanner = el('div', { class: 'callout callout-warning' },
                el('div', {}, el('strong', {}, 'No battery-backed RTC'),
                    el('div', { class: 'text-sm' }, 'This board has no real-time clock, so the system time resets on every power loss. Enable NTP, PTP, or GPS/GNSS synchronization, or re-enter the manual datetime after each boot.')));
        }
    } catch (_) { /* manifest optional — banner is best-effort */ }
    const t = cfg.time || {};
    const currentTimezone = t.timezone;
    const timezoneOptions = allTimezones().map(zone => ({ value: zone, label: zone.replace(/_/g, ' ') }));
    if (currentTimezone && !timezoneOptions.some(option => option.value === currentTimezone)) {
        timezoneOptions.unshift({ value: currentTimezone, label: `${currentTimezone} (current)` });
    }

    const timezone = Field({
        label: 'Timezone',
        type: 'select',
        value: currentTimezone,
        options: timezoneOptions,
        help: 'IANA timezone for displayed times and scheduled operations.',
    });
    const manualDt = Field({
        label: 'Manual datetime (air-gapped)',
        value: t.manual_datetime,
        help: 'Only used while NTP is disabled and the device cannot reach a time server.',
    });

    async function save() {
        await api.put('/api/v1/system', {
            hostname: cfg.hostname,
            asset_tag: cfg.asset_tag,
            hardware_revision: cfg.hardware_revision,
            serial_number: cfg.serial_number,
            time: {
                ...t,
                timezone: timezone.get() || null,
                manual_datetime: manualDt.get() || null,
            },
            ssh: cfg.ssh,
            ntp_servers: cfg.ntp_servers || [],
        });
        store.toast('Time settings saved', 'success');
    }

    async function apply() {
        await api.post('/api/v1/system/apply', {});
        store.toast('Time settings applied', 'success');
    }

    const timeCard = Card({
        title: 'Time and synchronization',
        subtitle: 'Set the timezone or adjust the clock manually for air-gapped systems.',
        footer: ActionList(
            ActionRow('Apply to device', 'Apply the timezone and clock synchronization settings now.', Button({ variant: 'outline', onClick: apply }, 'Apply')),
            ActionRow('Save settings', 'Store the time and synchronization settings for the next apply.', Button({ onClick: save }, 'Save'))),
    }, el('div', { class: 'form-grid' }, timezone.wrap, manualDt.wrap));

    return el('div', { class: 'time-page' },
        el('div', { class: 'page-header' },
            el('h1', {}, 'Clock'),
            el('p', {}, 'Configure the timezone and manual clock value. NTP, PTP, and GPS/GNSS have dedicated pages.')),
        rtcBanner,
        timeCard);
}
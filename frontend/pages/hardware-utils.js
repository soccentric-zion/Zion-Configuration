// Shared helpers for manifest-driven hardware pages.

export function peripheralsOf(data, kind) {
    return (data.manifest || []).filter(peripheral => peripheral.kind === kind);
}

export async function saveIo(api, data, updates) {
    // If-Match against the fingerprint this page loaded (io_version from
    // GET /io) — the server rejects the write with 409 if another editor's
    // change landed first, instead of silently clobbering it.
    const extraHeaders = data.io_version ? { 'If-Match': data.io_version } : undefined;
    await api.put('/api/v1/io', {
        serial: data.config && data.config.serial || [],
        serial_enabled: data.config && data.config.serial_enabled !== false,
        can: data.config && data.config.can || [],
        can_enabled: data.config && data.config.can_enabled !== false,
        gpio: data.config && data.config.gpio || [],
        gpio_enabled: data.config && data.config.gpio_enabled !== false,
        cameras: data.config && data.config.cameras || [],
        cameras_enabled: data.config && data.config.cameras_enabled !== false,
        mcu: data.config && data.config.mcu || null,
        board_leds: data.config && data.config.board_leds || {},
        ...updates,
    }, extraHeaders);
}

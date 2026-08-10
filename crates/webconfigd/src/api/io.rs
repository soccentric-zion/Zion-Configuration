//! I/O & peripheral handlers (spec §4.9). Configurable peripherals come from
//! the board manifest; USB and PCI inventory is read live from the host tools.

use axum::extract::{Path, State};
use axum::Json;
use serde::Serialize;
use tower_sessions::Session;
use webconfig_engine::manifest::IoKind;
use webconfig_engine::store::io::IoConfig;

use crate::api::{apply_and_audit, audit, username};
use crate::auth::AuthRole;
use crate::error::{ApiError, ApiResult};
use crate::state::AppState;

/// `GET /api/v1/io` — manifest-driven I/O + current config (spec §4.9).
pub async fn get(
    State(app): State<AppState>,
    _role: AuthRole,
) -> ApiResult<Json<serde_json::Value>> {
    let io = app.engine.load()?.io;
    let version = io_version(&io);
    Ok(Json(serde_json::json!({
        "manifest": app.manifest.peripherals,
        "config": io,
        "tiles": app.manifest.dashboard_tiles,
        // Fingerprint of the current IoConfig, for the PUT concurrency guard
        // below (Phase 5 polish: no version/ETag guard previously existed,
        // so two admins editing different peripherals concurrently could
        // silently clobber each other's read-modify-write).
        "io_version": version,
    })))
}

/// Short content fingerprint of an `IoConfig`, used as a simple ETag-like
/// concurrency token — no new persisted field/schema change needed.
fn io_version(io: &IoConfig) -> String {
    use sha2::{Digest, Sha256};
    let json = serde_json::to_vec(io).unwrap_or_default();
    let hash = Sha256::digest(json);
    hex::encode(&hash[..8])
}

/// `GET /api/v1/io/usb` - enumerate live USB devices with `lsusb`.
pub async fn usb_devices(
    State(app): State<AppState>,
    _role: AuthRole,
) -> ApiResult<Json<serde_json::Value>> {
    let (available, error, devices) = if app.engine.sysroot.is_real_root() {
        match std::process::Command::new("lsusb").output() {
            Ok(output) if output.status.success() => (
                true,
                None,
                parse_lsusb(&String::from_utf8_lossy(&output.stdout)),
            ),
            Ok(output) => (false, Some(command_error("lsusb", &output)), Vec::new()),
            Err(error) => (false, Some(error.to_string()), Vec::new()),
        }
    } else {
        (
            false,
            Some("hardware discovery unavailable in a fake sysroot".into()),
            Vec::new(),
        )
    };
    Ok(Json(
        serde_json::json!({ "available": available, "error": error, "devices": devices }),
    ))
}

/// `GET /api/v1/io/pci` - enumerate live PCI devices with `lspci`.
pub async fn pci_devices(
    State(app): State<AppState>,
    _role: AuthRole,
) -> ApiResult<Json<serde_json::Value>> {
    let (available, error, devices) = if app.engine.sysroot.is_real_root() {
        match std::process::Command::new("lspci")
            .args(["-D", "-nn"])
            .output()
        {
            Ok(output) if output.status.success() => (
                true,
                None,
                parse_lspci(&String::from_utf8_lossy(&output.stdout)),
            ),
            Ok(output) => (false, Some(command_error("lspci", &output)), Vec::new()),
            Err(error) => (false, Some(error.to_string()), Vec::new()),
        }
    } else {
        (
            false,
            Some("hardware discovery unavailable in a fake sysroot".into()),
            Vec::new(),
        )
    };
    Ok(Json(
        serde_json::json!({ "available": available, "error": error, "devices": devices }),
    ))
}

/// `GET /api/v1/io/i2c` - scan live I2C adapters with `i2cdetect`.
pub async fn i2c_devices(
    State(app): State<AppState>,
    _role: AuthRole,
) -> ApiResult<Json<serde_json::Value>> {
    if !app.engine.sysroot.is_real_root() {
        return Ok(Json(serde_json::json!({
            "available": false,
            "error": "hardware discovery unavailable in a fake sysroot",
            "devices": []
        })));
    }
    let buses = match std::fs::read_dir(app.engine.sysroot.join("/sys/class/i2c-dev")) {
        Ok(entries) => entries
            .filter_map(Result::ok)
            .filter_map(|entry| entry.file_name().into_string().ok())
            .filter_map(|name| {
                name.strip_prefix("i2c-")
                    .and_then(|value| value.parse::<u32>().ok())
            })
            .collect::<Vec<_>>(),
        Err(error) => {
            return Ok(Json(serde_json::json!({
                "available": false,
                "error": error.to_string(),
                "devices": []
            })));
        }
    };
    let mut devices = Vec::new();
    let mut errors = Vec::new();
    for bus in buses {
        match std::process::Command::new("i2cdetect")
            .args(["-y", &bus.to_string()])
            .output()
        {
            Ok(output) if output.status.success() => {
                devices.extend(parse_i2cdetect(
                    bus,
                    &String::from_utf8_lossy(&output.stdout),
                ));
            }
            Ok(output) => errors.push(command_error("i2cdetect", &output)),
            Err(error) => errors.push(error.to_string()),
        }
    }
    Ok(Json(serde_json::json!({
        "available": true,
        "error": if errors.is_empty() { None } else { Some(errors.join("; ")) },
        "devices": devices
    })))
}

/// `GET /api/v1/io/spi` - enumerate SPI controller/chip-select nodes.
/// SPI has no generic, safe bus-wide probe, so this reports kernel-created
/// spidev nodes and their bound driver metadata instead of clocking devices.
pub async fn spi_devices(
    State(app): State<AppState>,
    _role: AuthRole,
) -> ApiResult<Json<serde_json::Value>> {
    if !app.engine.sysroot.is_real_root() {
        return Ok(Json(serde_json::json!({
            "available": false,
            "error": "hardware discovery unavailable in a fake sysroot",
            "devices": []
        })));
    }
    let class_dir = app.engine.sysroot.join("/sys/class/spidev");
    let entries = match std::fs::read_dir(&class_dir) {
        Ok(entries) => entries,
        Err(error) => {
            return Ok(Json(serde_json::json!({
                "available": false,
                "error": error.to_string(),
                "devices": []
            })));
        }
    };
    let mut devices = entries
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let name = entry.file_name().into_string().ok()?;
            let (bus, chip_select) = parse_spidev_name(&name)?;
            let device_path = format!("/dev/{name}");
            let sysfs_device = entry.path().join("device");
            let modalias = std::fs::read_to_string(sysfs_device.join("modalias"))
                .ok()
                .map(|value| value.trim().to_string())
                .filter(|value| !value.is_empty())
                .unwrap_or_else(|| "-".into());
            let driver = std::fs::read_link(sysfs_device.join("driver"))
                .ok()
                .and_then(|path| {
                    path.file_name()
                        .map(|value| value.to_string_lossy().into_owned())
                })
                .unwrap_or_else(|| "-".into());
            Some(SpiDevice {
                name,
                bus,
                chip_select,
                device_path,
                modalias,
                driver,
            })
        })
        .collect::<Vec<_>>();
    devices.sort_by(|left, right| left.name.cmp(&right.name));
    Ok(Json(
        serde_json::json!({ "available": true, "error": null, "devices": devices }),
    ))
}

#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct UsbDevice {
    pub bus: String,
    pub device: String,
    pub vendor_id: String,
    pub product_id: String,
    pub description: String,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct PciDevice {
    pub address: String,
    pub class: String,
    pub vendor_id: String,
    pub product_id: String,
    pub revision: String,
    pub description: String,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct I2cDevice {
    pub bus: u32,
    pub address: String,
    pub status: String,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct SpiDevice {
    pub name: String,
    pub bus: u32,
    pub chip_select: u32,
    pub device_path: String,
    pub modalias: String,
    pub driver: String,
}

fn parse_lsusb(output: &str) -> Vec<UsbDevice> {
    output
        .lines()
        .filter_map(|line| {
            let mut fields = line.split_whitespace();
            if fields.next()? != "Bus" {
                return None;
            }
            let bus = fields.next()?.to_string();
            if fields.next()? != "Device" {
                return None;
            }
            let device = fields.next()?.trim_end_matches(':').to_string();
            if fields.next()? != "ID" {
                return None;
            }
            let ids = fields.next()?;
            let (vendor_id, product_id) = ids.split_once(':')?;
            if !is_hex_id(vendor_id) || !is_hex_id(product_id) {
                return None;
            }
            Some(UsbDevice {
                bus,
                device,
                vendor_id: vendor_id.to_lowercase(),
                product_id: product_id.to_lowercase(),
                description: fields.collect::<Vec<_>>().join(" "),
            })
        })
        .collect()
}

fn parse_lspci(output: &str) -> Vec<PciDevice> {
    output
        .lines()
        .filter_map(|line| {
            let (address, details) = line.split_once(' ')?;
            let (class, after_class) = details.split_once(" [")?;
            let class_end = after_class.find(']')?;
            let vendor_details = &after_class[class_end + 1..];
            let ids = vendor_details
                .match_indices('[')
                .filter_map(|(start, _)| {
                    let end = vendor_details[start + 1..].find(']')? + start + 1;
                    let id = &vendor_details[start + 1..end];
                    id.split_once(':')
                        .filter(|(vendor, product)| is_hex_id(vendor) && is_hex_id(product))
                })
                .next()?;
            let revision = details
                .split("(rev ")
                .nth(1)
                .and_then(|value| value.split(')').next())
                .unwrap_or("-")
                .to_string();
            Some(PciDevice {
                address: address.to_string(),
                class: class.to_string(),
                vendor_id: ids.0.to_lowercase(),
                product_id: ids.1.to_lowercase(),
                revision,
                description: details.to_string(),
            })
        })
        .collect()
}

fn parse_i2cdetect(bus: u32, output: &str) -> Vec<I2cDevice> {
    output
        .lines()
        .filter_map(|line| {
            let (row, cells) = line.split_once(':')?;
            let row = u8::from_str_radix(row.trim(), 16).ok()?;
            Some(
                cells
                    .split_whitespace()
                    .enumerate()
                    .filter_map(move |(column, value)| {
                        let address = row as u16 * 16 + column as u16;
                        if address > 0x7f || value == "--" {
                            return None;
                        }
                        let status = if value == "UU" {
                            "in-use"
                        } else {
                            "responding"
                        };
                        Some(I2cDevice {
                            bus,
                            address: format!("0x{address:02x}"),
                            status: status.into(),
                        })
                    }),
            )
        })
        .flatten()
        .collect()
}

fn parse_spidev_name(name: &str) -> Option<(u32, u32)> {
    let value = name.strip_prefix("spidev")?;
    let (bus, chip_select) = value.split_once('.')?;
    Some((bus.parse().ok()?, chip_select.parse().ok()?))
}

fn is_hex_id(value: &str) -> bool {
    value.len() == 4 && value.chars().all(|character| character.is_ascii_hexdigit())
}

fn command_error(command: &str, output: &std::process::Output) -> String {
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    if stderr.is_empty() {
        format!("{command} exited with {}", output.status)
    } else {
        stderr
    }
}

#[cfg(test)]
mod tests {
    use super::{parse_gpio_device, parse_i2cdetect, parse_lspci, parse_lsusb, parse_spidev_name};

    #[test]
    fn parses_lsusb_ids_and_description() {
        let devices = parse_lsusb(
            "Bus 001 Device 004: ID 0b1f:03ee Insyde Software Corp. RNDIS/Ethernet Gadget\n",
        );
        assert_eq!(devices.len(), 1);
        assert_eq!(devices[0].bus, "001");
        assert_eq!(devices[0].device, "004");
        assert_eq!(devices[0].vendor_id, "0b1f");
        assert_eq!(devices[0].product_id, "03ee");
        assert_eq!(
            devices[0].description,
            "Insyde Software Corp. RNDIS/Ethernet Gadget"
        );
    }

    #[test]
    fn parses_lspci_address_ids_class_and_revision() {
        let devices = parse_lspci(
            "0000:00:01.0 Host bridge [0600]: Advanced Micro Devices, Inc. [AMD] Device [1022:14da] (rev 01)\n",
        );
        assert_eq!(devices.len(), 1);
        assert_eq!(devices[0].address, "0000:00:01.0");
        assert_eq!(devices[0].class, "Host bridge");
        assert_eq!(devices[0].vendor_id, "1022");
        assert_eq!(devices[0].product_id, "14da");
        assert_eq!(devices[0].revision, "01");
    }

    #[test]
    fn ignores_malformed_inventory_lines() {
        assert!(parse_lsusb("not a usb line\nBus 001 Device 002: ID invalid nope\n").is_empty());
        assert!(
            parse_lspci("not a pci line\n0000:00:00.0 Host bridge [0600]: Unknown Device\n")
                .is_empty()
        );
    }

    #[test]
    fn parses_i2cdetect_addresses_and_in_use_state() {
        let devices = parse_i2cdetect(1, "     0 1 2 3 4 5 6 7 8 9 a b c d e f\n00: -- -- 03 UU -- -- -- -- -- -- -- -- -- -- -- --\n");
        assert_eq!(devices[0].address, "0x02");
        assert_eq!(devices[0].status, "responding");
        assert_eq!(devices[1].address, "0x03");
        assert_eq!(devices[1].status, "in-use");
    }

    #[test]
    fn parses_spidev_bus_and_chip_select() {
        assert_eq!(parse_spidev_name("spidev0.1"), Some((0, 1)));
        assert_eq!(parse_spidev_name("spi0.0"), None);
    }

    #[test]
    fn parses_gpio_device_chip_and_line() {
        // Manifest form: "gpiochip1 line 7" -> chip + line offset.
        assert_eq!(
            parse_gpio_device("gpiochip1 line 7"),
            Some(("gpiochip1", "7"))
        );
        assert_eq!(
            parse_gpio_device("gpiochip2 line 5"),
            Some(("gpiochip2", "5"))
        );
    }

    #[test]
    fn rejects_malformed_gpio_device() {
        assert_eq!(parse_gpio_device("gpiochip1"), None);
        assert_eq!(parse_gpio_device("gpiochip1 offset 7"), None);
        assert_eq!(parse_gpio_device(""), None);
    }
}

/// `PUT /api/v1/io` — update serial/CAN/GPIO/MCU config (admin only).
pub async fn put(
    State(app): State<AppState>,
    role: AuthRole,
    session: Session,
    headers: axum::http::HeaderMap,
    Json(req): Json<IoConfig>,
) -> ApiResult<Json<IoConfig>> {
    role.require_admin("io.put")?;
    // Optimistic-concurrency guard (Phase 5 polish): a client that sent the
    // io_version it loaded via GET /io as If-Match is rejected with 409 if
    // the config changed underneath it — silent-clobber prevention, not a
    // hard requirement (older/other clients that omit If-Match still work
    // exactly as before).
    if let Some(if_match) = headers.get("if-match").and_then(|v| v.to_str().ok()) {
        let current_version = io_version(&app.engine.load()?.io);
        if if_match != current_version {
            return Err(ApiError::Conflict(
                "io config changed since you loaded it — reload and retry".into(),
            ));
        }
    }
    // Serial ownership conflict prevention (spec §4.9): no two ports may claim
    // the same non-unassigned owner when the owner is a singleton like console,
    // and no two ports may target the same device (ownership is per-device).
    let mut owners = std::collections::HashSet::new();
    let mut devices = std::collections::HashSet::new();
    for s in &req.serial {
        if s.owner != "unassigned" && !owners.insert(s.owner.clone()) {
            return Err(ApiError::Conflict(format!(
                "serial owner {} claimed twice",
                s.owner
            )));
        }
        if !devices.insert(s.device.clone()) {
            return Err(ApiError::Conflict(format!(
                "serial device {} claimed twice",
                s.device
            )));
        }
        // Reject ports not in the manifest.
        if !app.manifest.has(&s.device.replace("/dev/", ""))
            && !app
                .manifest
                .peripherals
                .iter()
                .any(|p| p.device == s.device)
        {
            return Err(ApiError::BadRequest(format!(
                "serial {} not in manifest",
                s.device
            )));
        }
    }
    let user = username(&session).await;
    let cfg = app.engine.update(move |c| {
        c.io = req.clone();
        Ok(())
    })?;
    audit(&app, &user, "io.put", None);
    Ok(Json(cfg.io))
}

/// `POST /api/v1/io/can/apply` — apply the CAN applier (spec §4.9).
pub async fn apply_can(
    State(app): State<AppState>,
    role: AuthRole,
    session: Session,
) -> ApiResult<Json<serde_json::Value>> {
    role.require_admin("io.can.apply")?;
    let user = username(&session).await;
    app.engine.validate("can").await?;
    let _ = apply_and_audit(&app, &user, "can").await?;
    Ok(Json(serde_json::json!({ "applied": ["can"] })))
}

/// `POST /api/v1/io/serial/apply` — apply the serial applier (spec §4.9): set
/// per-port baud and resolve exclusive ownership (console getty on/off).
pub async fn apply_serial(
    State(app): State<AppState>,
    role: AuthRole,
    session: Session,
) -> ApiResult<Json<serde_json::Value>> {
    role.require_admin("io.serial.apply")?;
    let user = username(&session).await;
    app.engine.validate("serial").await?;
    let _ = apply_and_audit(&app, &user, "serial").await?;
    Ok(Json(serde_json::json!({ "applied": ["serial"] })))
}

/// `POST /api/v1/io/gpio/:name/toggle` — manual toggle for an output (spec §4.9).
/// Reads the current line level and drives the opposite; output pins only.
pub async fn toggle_gpio(
    State(app): State<AppState>,
    role: AuthRole,
    session: Session,
    Path(name): Path<String>,
) -> ApiResult<Json<serde_json::Value>> {
    role.require_mutate_apps("io.gpio.toggle")?;
    // Manifest-named pins only (spec §4.9 — never raw banks).
    let p = app
        .manifest
        .peripherals
        .iter()
        .find(|p| p.id == name && p.kind == IoKind::Gpio)
        .ok_or_else(|| ApiError::BadRequest("gpio not in manifest".into()))?;
    // Only output lines can be driven — reject inputs (e.g. the recovery
    // button) so a toggle never tries to drive a read-only line (spec §4.9).
    let is_output = p
        .caps
        .get("direction")
        .and_then(|v| v.as_str())
        .map(|d| d == "out")
        .unwrap_or(false);
    if !is_output {
        return Err(ApiError::BadRequest(format!(
            "gpio {name} is not an output"
        )));
    }
    let (chip, line) = parse_gpio_device(&p.device)
        .ok_or_else(|| ApiError::BadRequest(format!("gpio {name} device malformed")))?;
    let value = if app.engine.sysroot.is_real_root() {
        // Read the current level with `gpioget`, then drive the opposite with
        // `gpioset` (assumption per spec §7: libgpiod CLI). Previously this
        // always drove `1=1` against a malformed arg, so "toggle" was a no-op
        // that only ever set high.
        let cur = webconfig_engine::applier::run_cmd("gpioget", &[chip, line])
            .ok()
            .and_then(|s| s.trim().parse::<u8>().ok())
            .unwrap_or(0);
        let next = if cur == 1 { 0 } else { 1 };
        let spec = format!("{line}={next}");
        let _ = webconfig_engine::applier::run_cmd("gpioset", &[chip, &spec]);
        next
    } else {
        // Fake sysroot has no real line state; report a deterministic toggle.
        1
    };
    audit(
        &app,
        &username(&session).await,
        "io.gpio.toggle",
        Some(name.clone()),
    );
    Ok(Json(serde_json::json!({ "pin": name, "value": value })))
}

/// Parse a manifest GPIO `device` string of the form `gpiochip1 line 7` into
/// its chip id and line offset, the shape `gpioset`/`gpioget` expect.
fn parse_gpio_device(device: &str) -> Option<(&str, &str)> {
    let parts: Vec<&str> = device.split_whitespace().collect();
    if parts.len() == 3 && parts[1] == "line" {
        Some((parts[0], parts[2]))
    } else {
        None
    }
}

/// `POST /api/v1/io/i2c/scan` — admin-only bus scan (spec §4.9).
pub async fn i2c_scan(
    State(app): State<AppState>,
    role: AuthRole,
    axum::extract::Query(q): axum::extract::Query<BusQuery>,
) -> ApiResult<Json<serde_json::Value>> {
    role.require_admin("io.i2c.scan")?;
    if !app
        .manifest
        .peripherals
        .iter()
        .any(|p| p.kind == IoKind::I2c && p.id == q.bus)
    {
        return Err(ApiError::BadRequest("i2c bus not in manifest".into()));
    }
    let addrs = if app.engine.sysroot.is_real_root() {
        let out =
            webconfig_engine::applier::run_cmd("i2cdetect", &["-y", &q.bus]).unwrap_or_default();
        // Parse hex addresses from the i2cdetect table.
        out.lines()
            .flat_map(|l| l.split_whitespace().skip(1))
            .filter_map(|t| u16::from_str_radix(t.trim_end_matches(':'), 16).ok())
            .collect::<Vec<_>>()
    } else {
        Vec::new()
    };
    Ok(Json(
        serde_json::json!({ "bus": q.bus, "addresses": addrs }),
    ))
}

#[derive(serde::Deserialize)]
pub struct BusQuery {
    pub bus: String,
}

/// `GET /api/v1/io/mcu` — companion MCU panel (spec §4.9): fw version,
/// heartbeat, settings via the schema engine.
pub async fn mcu(
    State(app): State<AppState>,
    _role: AuthRole,
) -> ApiResult<Json<serde_json::Value>> {
    let m = app
        .manifest
        .of_kind(IoKind::Mcu)
        .into_iter()
        .next()
        .cloned();
    let cfg = app.engine.load()?.io.mcu;
    Ok(Json(serde_json::json!({
        "manifest": m,
        "config": cfg,
    })))
}

/// Firmware bodies are STM32 images — a few hundred KB at most. Bounded well
/// under the spec §2 RAM budget so a malicious/broken client can't buffer an
/// arbitrarily large body into memory (same concern as Checkpoint 1.8, applied
/// locally here since no global body-limit layer exists yet).
const MAX_MCU_FIRMWARE_BYTES: usize = 4 * 1024 * 1024;

#[derive(serde::Deserialize)]
pub struct FirmwareUploadQuery {
    pub version: String,
    /// Detached Ed25519 signature over the raw firmware bytes, hex-encoded
    /// (128 hex chars = 64 bytes).
    pub signature: String,
}

/// `POST /api/v1/io/mcu/firmware?version=...&signature=<hex>` — real firmware
/// bytes upload + real signature verification + flash (spec §4.9). No
/// multipart dependency: the request body *is* the firmware image
/// (`application/octet-stream`); metadata travels in the query string.
///
/// Verification uses Ed25519 (via the already-vendored `ring` crate — no new
/// dependency) against a manifest-declared public key
/// (`caps.firmware_pubkey_ed25519` on the `mcu` peripheral), the same
/// "trust anchor baked into platform data" pattern RAUC uses for OS bundles
/// (there RAUC's own keyring; here the board manifest). A client-supplied
/// `signed` boolean is never trusted — the signature is cryptographically
/// verified against the firmware bytes actually received.
///
/// Flashing: a real flash operation is invoked when the manifest declares a
/// `caps.flash_command` for the MCU peripheral (shelled out on the real
/// root); when no flash mechanism is declared for this platform, the response
/// explicitly reports `flashed: false` with a `not implemented` reason rather
/// than fabricating success. On the fake sysroot, verification and the
/// on-disk write still happen for real, but flashing is always reported as
/// simulated (spec §6/§7: never claim a real flash without real hardware).
pub async fn mcu_firmware(
    State(app): State<AppState>,
    role: AuthRole,
    session: Session,
    axum::extract::Query(q): axum::extract::Query<FirmwareUploadQuery>,
    body: axum::body::Bytes,
) -> ApiResult<Json<serde_json::Value>> {
    role.require_admin("io.mcu.firmware")?;
    let user = username(&session).await;

    if body.is_empty() {
        return Err(ApiError::BadRequest("firmware body is empty".into()));
    }
    if body.len() > MAX_MCU_FIRMWARE_BYTES {
        return Err(ApiError::BadRequest(format!(
            "firmware body exceeds {MAX_MCU_FIRMWARE_BYTES} bytes"
        )));
    }

    let mcu = app
        .manifest
        .of_kind(IoKind::Mcu)
        .into_iter()
        .next()
        .ok_or_else(|| {
            ApiError::BadRequest("no MCU peripheral declared in board manifest".into())
        })?;

    let pubkey_hex = mcu
        .caps
        .get("firmware_pubkey_ed25519")
        .and_then(|v| v.as_str())
        .ok_or_else(|| {
            ApiError::Conflict(
                "no firmware_pubkey_ed25519 configured for this platform's MCU — firmware \
                 upload is refused rather than accepted unverified"
                    .into(),
            )
        })?;
    let pubkey = hex::decode(pubkey_hex).map_err(|_| {
        ApiError::Engine("manifest firmware_pubkey_ed25519 is not valid hex".into())
    })?;
    let signature = hex::decode(&q.signature)
        .map_err(|_| ApiError::BadRequest("signature is not valid hex".into()))?;

    let verify_key = ring::signature::UnparsedPublicKey::new(&ring::signature::ED25519, &pubkey);
    if verify_key.verify(&body, &signature).is_err() {
        audit(
            &app,
            &user,
            "io.mcu.firmware",
            Some(format!("{}: signature rejected", q.version)),
        );
        return Err(ApiError::BadRequest(
            "firmware signature verification failed".into(),
        ));
    }

    // Signature is real and verified — land the bytes on disk.
    let fw_dir = app.engine.sysroot.webconfig_dir().join("mcu-firmware");
    std::fs::create_dir_all(&fw_dir)?;
    let safe_version: String = q
        .version
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '.' || *c == '-' || *c == '_')
        .collect();
    if safe_version.is_empty() {
        return Err(ApiError::BadRequest(
            "version must be non-empty and safe".into(),
        ));
    }
    let fw_path = fw_dir.join(format!("{safe_version}.bin"));
    std::fs::write(&fw_path, &body)?;

    let (flashed, reason) = if !app.engine.sysroot.is_real_root() {
        (
            false,
            "simulated (fake sysroot, no real hardware)".to_string(),
        )
    } else if let Some(cmd) = mcu.caps.get("flash_command").and_then(|v| v.as_str()) {
        match webconfig_engine::applier::run_cmd(cmd, &[fw_path.to_string_lossy().as_ref()]) {
            Ok(_) => (true, "ok".to_string()),
            Err(e) => (false, format!("flash command failed: {e}")),
        }
    } else {
        (
            false,
            "not implemented: no flash_command declared for this platform".to_string(),
        )
    };

    audit(
        &app,
        &user,
        "io.mcu.firmware",
        Some(format!(
            "{}: signature ok, {} bytes, flashed={flashed} ({reason})",
            q.version,
            body.len()
        )),
    );
    Ok(Json(serde_json::json!({
        "flashed": flashed,
        "reason": reason,
        "version": q.version,
        "bytes_written": body.len(),
    })))
}

/// Platform panel (spec §4.9): board-specific data, driven entirely by the
/// compiled-in board manifest (platform, model, cameras). No hardcoded
/// per-SoC power-mode lists here — the manifest is the single source of
/// board truth, so the same code serves every platform.
pub async fn platform_panel(
    State(app): State<AppState>,
    _role: AuthRole,
) -> ApiResult<Json<serde_json::Value>> {
    let m = &app.manifest;
    Ok(Json(serde_json::json!({
        "platform": m.platform,
        "model": m.model,
        "board_name": m.board_name,
        "cameras": m.of_kind(IoKind::Camera),
    })))
}

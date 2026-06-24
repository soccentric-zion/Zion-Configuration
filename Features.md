# Feature Improvements — Embedded Dashboard (Multi-Platform)

## Purpose

This document specifies the next-generation feature set for the embedded Linux device dashboard. The dashboard is a single codebase that runs across all six SoCcentric platforms, with every feature, page, and sub-feature switched on or off by a per-device `config.yaml`. The guiding rule: **if a feature is declared in `config.yaml`, it is displayed; if it is absent, it does not exist — the backend does not register the route and the frontend does not render the menu item or panel.**

This builds on the existing stack: **FastAPI + uvicorn + Vue 3 (CDN) + PrimeVue + SQLite (aiosqlite) + xterm.js + WebSocket**, with a HAL abstraction that runs real hardware on-device and deterministic simulation on a dev machine.

---

## Target Platforms

| Platform | SoC | Co-processor | Co-proc chip | Transport | On/Off-chip |
|----------|-----|--------------|--------------|-----------|-------------|
| Arches | NVIDIA Jetson | STM32 | STM32 MCU | UART / SPI | Off-chip |
| Acadia | Raspberry Pi CM4/CM5 | Pico W | RP2040 | UART / SPI | Off-chip |
| Zion | Xilinx Zynq | FPGA fabric | Zynq PL | MMIO / AXI | On-chip (same die) |
| Pinnacle | NXP i.MX | Cortex-M7 / M4 | i.MX integrated MCU | RPMsg / shared memory | On-chip |
| Joshua | TI Sitara | PRU | TI PRU cores | Shared memory / RPMsg | On-chip |
| Sequoia | Intel / AMD x86 | RP2040 | RP2040 | UART / SPI (USB-CDC) | Off-chip |

Co-processor transports cluster into three HAL backends:
- **UART/SPI** (off-chip serial, incl. USB-CDC `/dev/ttyACM*`) — Arches, Acadia, Sequoia
- **RPMsg / shared-memory** (on-chip) — Joshua, Pinnacle
- **MMIO / AXI** (on-chip fabric) — Zion

---

## Core Principle — Config-Driven Everything (Req 1)

### Behavior
- **Platform identity** is hardcoded in `config.yaml` (`platform.name`).
- **Granularity** is down to the sub-feature: groups → panels → individual sub-features are each independently toggleable.
- **Absent = hidden completely.** No greying out. The backend refuses (does not register) routes for disabled features; the frontend builds the entire sidebar and all panels from the fetched feature tree.

### Mechanism
1. `config.yaml` declares `platform.name` and a nested feature tree.
2. On startup the backend loads and validates the config (pydantic), and conditionally registers only the routers/endpoints for enabled features.
3. Backend exposes the enabled tree at `GET /api/config/features`.
4. Frontend fetches that tree first, then constructs the sidebar, routes, and panels dynamically.

### Two enforcement points (both mandatory)
- **Backend:** disabled features have no reachable route. Security is enforced server-side, never by UI hiding alone.
- **Frontend:** the menu and panels are rendered purely from the fetched feature tree.

### Example config shape
```yaml
platform:
  name: Arches

features:
  overview:
    device_identity: true
    health_status: true
    live_metrics:
      cpu: true
      gpu: true
      ram: true
      temperature: true
  hardware:
    gpio: true
    coprocessor: true
    gpu: true
    camera: true

coprocessor:
  enabled: true
  type: stm32              # stm32 | pico | pru | fpga | imx_mcore | rp2040
  transport: uart          # uart | spi | rpmsg | mmio | shmem
  port: /dev/ttyTHS1
  baud: 921600
  protocol: custom_v1
  fota:
    enabled: true
    method: stm32_bootloader   # stm32_bootloader | uf2 | serial | remoteproc | fpga_manager | pru_reload
    enter_bootloader: gpio_boot0
```

---

## Co-processor Subsystem (cross-cutting, Req 1 continued)

The co-processor is a first-class, configurable subsystem shared by the Hardware, OTA/FOTA, Thermal, and Logs sections.

- **One co-processor HAL interface**, three transport implementations (UART/SPI, RPMsg/shmem, MMIO/AXI).
- **Unified panel UX** regardless of transport: link status, firmware version, round-trip latency, last message, error counters.
- **FOTA over the same channel** the dashboard already uses (see OTA/FOTA section).

---

## Req 2 — Overview / Home Page

The landing page after login. At-a-glance "is my device OK right now?" view. All items config-gated; live metrics stream over WebSocket.

### Device Identity
- Platform name, hostname, serial number
- Hardware revision, software/firmware version
- Kernel / OS version, bootloader version
- IP address(es), MAC address(es)

### Health & Status
- Overall health badge (OK / warning / critical — rolled up from thresholds)
- Uptime
- Active alerts count
- Co-processor link status
- Throttling indicator
- Simulation badge (shown when any HAL is mocked)
- Last OTA / firmware update date
- Current time + timezone

### Live Metrics (WebSocket)
- CPU usage (aggregate + per-core)
- RAM + swap usage
- Load average
- Temperature
- Basic sensors
- Disk / storage usage
- Network throughput (RX / TX)

### Power & Thermal
- Power source + input voltage
- Throttling status

---

## Req 3 — Network

PrimeVue sidebar (shadcn-style), each sub-category its own page. **View + edit/apply.** All edits via ConfirmDialog; connectivity-affecting changes use a 60-second auto-rollback (if the dashboard becomes unreachable after apply, revert automatically). Every sub-page and feature config-gated.

### Interfaces
- Interface list: IP v4/v6, MAC, link state, speed, RX/TX, live bandwidth
- DHCP/static toggle, address/mask/gateway
- Bonding/teaming (failover, LACP)
- VLAN tagging, MTU, Wake-on-LAN

### WiFi
- SSID, signal, frequency, security; scan + connect; saved networks
- Access Point mode (hotspot — for headless first-setup)
- Hidden SSID connect
- Region/country code, band selection (2.4 / 5 GHz)

### DNS / Routing
- Hostname, DNS servers, default gateway, routing table
- Static route management
- mDNS / Avahi toggle, per-interface DNS

### Firewall (status + link; full management lives in Security)
- Ruleset viewer per zone/chain, enable/disable, open ports
- Port forwarding / NAT
- Connection tracking
- IP blocklist

### Cellular / Modem
- Carrier, signal, technology, APN config, data usage

### VPN
- WireGuard / OpenVPN config, peer status, handshake

### Time / NTP
- NTP servers, sync status

### Proxy
- System proxy settings (for industrial networks behind corporate proxies)

---

## Req 4 — Hardware

Every sub-page config-gated; each its own page. A platform shows only what its `config.yaml` declares.

### GPIO
- Pin list with direction/value, toggle outputs, live input state
- Export/unexport, pin labeling/aliasing, pull up/down, edge detection

### PWM
- Per-channel period/duty sliders, enable/disable, polarity, frequency presets

### ADC
- Live readings per channel, scaling/calibration, sample rate, min/max thresholds

### Co-processor
- Link status, firmware version, RTT latency, message log, error counters
- FOTA, transport config (UART/SPI/RPMsg/MMIO)

### Sensors
- Auto-discovered hwmon/IIO: temp, humidity, pressure, voltage, current, accel/gyro/IMU, light
- Live charts, min/max/avg, calibration, polling interval

### I2C
- Bus list, address scan grid, register read/write, device probe, bus speed

### SPI
- Device list, mode/speed config, raw transfer, CS config

### UART / Serial
- Port list, baud/parity/stop config, terminal, loopback test

### CAN bus
- Interface state, bitrate, frame monitor/inject, filters, error counters, CAN-FD

### USB
- Device tree, per-port power control, mount status, bandwidth

### Display
- Connected displays, resolution/orientation, brightness, backlight, multi-display layout

### Camera (Arches especially)
- Detected cameras, resolution/format, live preview, capture, per-sensor controls

### Audio
- Input/output devices, volume, routing, test tone

### GPU (Arches)
- CUDA utilization, memory, temp/power, clock, loaded models, inference rate

### FPGA (Zion)
- Bitstream version, fabric status, PL temp, device tree overlay, reconfigure

### PRU (Joshua)
- PRU state, loaded firmware, reload, shared-memory view

### LEDs
- Discoverable LEDs, trigger/brightness control, patterns

### Relays / Actuators
- Relay toggle, motor/stepper control, actuator status

### Buttons / Inputs
- Button state, rotary encoders, input event monitor

### Device Tree Overlays
- List active/available overlays, enable/disable, apply

### Clocks / Timers
- Clock tree, frequency scaling, RTC

### Pin Multiplexing
- Pinmux state viewer, function assignment per pin

---

## Req 5 — Storage

Every sub-page config-gated; each its own page.

### Disks / Block Devices
- Device list (eMMC, SD, NVMe, SATA, USB), size, model, serial
- Partition table viewer, mount/unmount, mount points + usage
- Read/write throughput live

### eMMC Health
- Lifetime estimate, pre-EOL info, bad blocks, wear leveling, extcsd registers

### SD Card
- Presence, size, speed class, health, read-only detect

### SMART (NVMe/SATA — Sequoia)
- Health summary, attributes, temperature, power-on hours, self-test trigger

### Filesystems
- Mounted FS list, type, usage, inode usage
- fsck trigger, resize, mount options, fstab editor

### Partitions
- Create/delete/resize, partition table type (GPT/MBR), flags

### RAID / LVM
- Array status, volume groups, logical volumes, create/manage

### A/B Partitions
- Slot status, active/fallback, switch, rollback (ties into OTA)

### Mount Manager
- Auto-mount rules, network mounts (NFS/CIFS/SMB), USB automount

### Quotas
- Per-user/per-path disk quotas, usage

### Encryption
- LUKS volume status, unlock, key management

### Swap
- Swap devices/files, size, swappiness, zram config

### Disk Cleanup
- Largest files/dirs, log rotation status, temp cleanup, cache clear

### Backup Targets
- Configured backup destinations, USB/network targets, scheduled jobs

---

## Req 6 — Thermal

Every sub-page config-gated; each its own page.

### Thermal Zones
- All zones with live temperature, zone labels, critical/trip thresholds, live multi-zone chart

### Trip Points
- Per-zone trip points (active/passive/hot/critical), editable thresholds, action per trip

### Fan Control
- Fan list, RPM readout, PWM/manual control, auto mode, fan curves (temp→speed), min/max limits

### Cooling Policy
- Active/passive selection, per-zone cooling device binding, governor

### Throttling
- Current vs max CPU/GPU frequency, throttle event counter, throttle log, thermal pressure

### Heat Map
- 24h time × zone visualization (SQLite-backed), selectable window

### Thermal History
- Long-term temp logging, min/max/avg per zone, export

### Alerts / Limits
- Per-zone alert thresholds, over-temp event log, emergency shutdown config

### GPU Thermal (Arches)
- GPU temp zones, GPU fan, GPU throttle status

### FPGA Thermal (Zion)
- PL fabric temperature, junction temp, fabric-specific limits

### Co-processor Thermal
- MCU temperature if exposed over the channel

### Cooling Devices
- Discoverable cooling devices, current/max state, manual override

---

## Req 7 — OTA & FOTA

Every sub-page config-gated. Unifying UX: every update target (system, co-processor, FPGA) uses the same panel pattern — **upload → progress → verify → version confirm → history** — with different backends underneath.

### System OTA (SWUpdate)
- Current installed version set (`/etc/sw-versions`)
- Upload `.swu` (chunked, progress), stream to SWUpdate
- Live install progress (bridged from SWUpdate WebSocket)
- A/B partition state before/after, post-install reboot now/later
- Update history (version, date, result, duration, who)
- Rollback trigger
- hawkBit status (if suricatta configured)

### Co-processor FOTA (over the channel)
- Per co-processor firmware upload over the same link the dashboard uses
- Method per type: STM32 bootloader, RP2040 UF2/serial, remoteproc (i.MX M-core, PRU), FPGA manager (Zion bitstream)
- Enter-bootloader handling (GPIO BOOT0, reset sequence)
- Progress + verify, current vs new version
- Per-target history

### FPGA Bitstream (Zion)
- Bitstream version, upload + reconfigure via FPGA manager, device tree overlay reload

### Update Channels
- Channel selection (stable/beta/dev), update server URL config

### Scheduling
- Scheduled/deferred updates, maintenance windows, auto-update toggle

### Delta / Differential
- Delta update support, bandwidth saved

### Verification
- Signature/checksum verification, key management, anti-rollback counter

### Staging / Approval
- Download-now-install-later, staged rollout, approval gate before apply

### Bundle Management
- Uploaded bundle list, integrity check, delete, metadata viewer

### Recovery
- Recovery mode trigger, failsafe image, factory image restore

### Fleet / Remote (if hawkBit)
- Device group, deployment status, remote-triggered updates

---

## Req 8 — Telemetry

Multi-protocol, config-selectable metrics, store-and-forward. Config-gated. Rides on top of the Cloud connection (Req 9) as the data-sending layer.

### Cloud Connection
- Provider/protocol: MQTT, HTTP/REST, AWS IoT, Azure IoT Hub, generic broker
- Endpoint config: host, port, TLS, client ID
- Credentials reference (cert/key/token — user-entered, never auto-filled)
- Connection status, last connect, reconnect/backoff state, test connection

### Metric Selection
- Catalog of all available metrics (CPU, RAM, temp, sensors, network, co-processor, etc.)
- Per-metric enable/disable (config-selectable)
- Per-metric send interval, payload format (JSON, etc.)

### Store & Forward
- Offline queue depth, oldest-item age
- Queue size/retention limits, disk-backed buffer
- Flush-on-reconnect, drop policy when full
- Bandwidth used (daily)

### Scheduling / Batching
- Send interval, batch size, compression, burst vs steady cadence

### Topics / Routing (MQTT)
- Topic templates per metric, QoS level, retain flag

### Last Sync
- Last successful send, success/fail counters, error log

### Retention Policy
- Local history retention (days), enforcement job

### Security
- TLS cert management, payload encryption, mutual TLS status

---

## Req 9 — Cloud

Multi-provider connectivity. Cloud is the connection/identity/management layer; Telemetry (Req 8) is the metric-streaming layer that uses it. Keep as separate pages but **share the same underlying connection config** so endpoints aren't entered twice. Config-gated.

### Provider Selection
- AWS IoT Core, Azure IoT Hub, Google Cloud (IoT / Pub-Sub), custom MQTT/HTTP endpoint
- Per-provider enable, switch active provider

### Connection Config
- Endpoint/host, port, region, TLS
- Client/device ID, thing name
- Credentials reference (cert/key/token/connection string — user-entered, never auto-filled)
- Test connection, connection status, last handshake

### Provisioning
- Device registration state (unprovisioned → claimed → registered → active)
- Claim token generate/display/revoke
- Certificate provisioning status, auto-provisioning (AWS fleet provisioning, Azure DPS)
- Bootstrap endpoint config

### Device Twin / Shadow
- View reported/desired state, sync status, desired-state delta handling
- Apply desired changes locally

### Messaging
- Cloud-to-device commands, device-to-cloud events
- Topic/route mapping, QoS, command handler status

### Certificates / Auth
- X.509 cert lifecycle, expiry warnings, rotation
- IAM/SAS token management, mutual TLS status

### Remote Management
- Remote config pull, remote command execution (gated), remote reboot/update trigger
- Jobs/deployments status (AWS IoT Jobs, Azure deployments)

### Connection Resilience
- Reconnect/backoff, offline behavior, heartbeat/keepalive, last-will message

### Status / Diagnostics
- Connection uptime, message counters, error log, latency to cloud

---

## Req 10 — Security

Config-gated. **Firewall is canonical here**; Network shows status + links here to avoid drift. **Users/roles/groups are canonical in User Management (Req 11)**; Security references them and focuses on firewall/certs/hardening.

### Firewall
- Ruleset viewer per zone/chain (nftables/iptables)
- Add/edit/delete rules, enable/disable per zone
- Port forwarding/NAT, default policies
- IP allowlist/blocklist, rate limiting

### SSH
- Service status, port, key management (authorized_keys + fingerprints)
- Password vs key-only auth toggle, root login toggle

### Certificates
- Cert list (subject/issuer/expiry), expiry warnings (<30 days)
- Upload cert/key, generate self-signed, CA management, rotation

### Secrets / Keys
- Key store status, TPM/secure element status, key generation
- Disk encryption (LUKS) status

### Access Control
- Role-permission matrix, per-endpoint enforcement view
- API key management, token expiry

### Audit Trail
- Append-only log of every action (who/what/when/IP/old→new)
- Filter by user/date/action, CSV export

### Intrusion / Hardening
- fail2ban-style blocking, brute-force detection
- Secure boot status, kernel hardening flags, SELinux/AppArmor status

### Network Security
- Open ports, active connections, VPN status
- TLS config for the dashboard itself (HTTPS cert)

### Compliance
- Security posture summary, CVE scan results, SBOM viewer
- Last audit date, certification IDs

### Updates / Patching
- Security update status, anti-rollback counter, signature verification

---

## Req 11 — User Management

Config-gated. Canonical home for users, roles, groups, permissions.

### Users
- List/create/edit/delete; username, full name, email, role
- Set/reset password (bcrypt), enable/disable, force change on next login
- Account expiry, last login, lock/unlock

### Roles
- Built-in (admin/operator/viewer) + custom roles
- Create/edit/delete, granular per-group/page/action permission assignment, role cloning

### Groups
- Create/edit/delete, assign users, group-based permissions, nested groups/inheritance

### Permissions
- Permission matrix (role/group × resource × action)
- Read/write/execute granularity per feature
- Effective-permissions viewer

### Authentication
- Password policy (length, complexity, expiry, history)
- 2FA/MFA (TOTP) per user
- Session timeout, max concurrent sessions
- Login method: local, LDAP/AD, OAuth/SSO

### Sessions
- Active sessions (user, IP, start, last activity)
- Remote kill/force-logout, session history

### Access Policy
- Lockout after N failed attempts, lockout duration
- IP allowlist per user, time-based access windows, failed login log

### Directory Integration
- LDAP/Active Directory config, sync, group mapping
- SSO/OAuth provider config

### Audit
- Per-user action history, login/logout events, permission change log (append-only)

### Self-Service
- Change own password, manage own 2FA, view own sessions

---

## Req 12 — Logs

Config-gated. **Hard requirement: all file access is path-traversal protected (canonicalize + allowlist prefix check).** Viewer role gated appropriately since logs can expose sensitive data.

### Journal (journalctl)
- Live follow (WebSocket), unit filter, priority filter
- Time range, free-text search, boot selection, severity highlight, jump to errors

### dmesg / Kernel
- Kernel ring buffer, live follow, severity filter, subsystem filter, timestamp formatting

### Syslog
- /var/log/syslog, messages, live tail, filter

### Application Logs
- App-specific log files (config-declared paths), live follow, multi-file
- Per-app log level adjustment where supported

### Service Logs
- Per-systemd-unit logs, last N lines, live follow for selected unit

### Co-processor Logs
- Messages/errors from the co-processor channel

### Audit Logs
- Security/audit trail viewer (cross-link to Security)

### Boot Logs
- Boot messages, systemd-analyze, boot-time breakdown

### Custom Log Files
- Any file under allowed paths (traversal-protected), live tail

### Filtering / Search
- Global search across sources, regex, time-window, severity, saved filters/views

### Export
- Export current view (.txt), full journal export (compressed), date-range, scheduled

### Log Management
- Log rotation status, disk usage by logs, retention config
- Clear/vacuum journal (admin), rate-limit config

### Live Stream
- Multi-source combined live view, pause/resume, auto-scroll toggle, line wrap

---

## Settings (dashboard-level)

Config-gated. Key behavior: **dashboard users normally map to system users**, with a toggle to enable dashboard access per system user (PAM-backed), falling back to dashboard-local accounts where preferred.

### Dashboard Users
- Add/remove dashboard users (maps to system users by default)
- Toggle: dashboard-local vs system-user-backed (PAM)
- Enable/disable dashboard access per system user, role assignment, link to User Management

### Dashboard Config
- Runtime feature enable/disable (reflects yaml where overridable)
- Default landing page, items per page, refresh intervals, session timeout, idle lockout

### Appearance
- Theme (dark/light/auto), accent color, language/locale, date/time format, units, density, sidebar default

### System Settings
- Hostname, timezone, NTP, time sync, locale, keyboard layout, reboot, shutdown

### Access & Auth
- Auth method (local/PAM/LDAP/SSO), password policy, 2FA enforcement, HTTPS/TLS cert, port

### Network Defaults
- Bind address/port, allowed origins (CORS), behind-proxy settings

### Notifications
- Alert channels (email/webhook/toast), SMTP config, per-event toggles

### Backup / Restore
- Export/import dashboard config (encrypted), scheduled backup, restore points, config diff
- Factory reset (triple confirm)

### Integrations
- SWUpdate endpoint, cloud provider link, telemetry endpoint, co-processor channel defaults

### Maintenance
- Update dashboard itself, clear cache/data, DB size + vacuum, diagnostic bundle export, dashboard log level

### About
- Dashboard version, platform name, build date, license info, open-source attribution, SBOM

---

## Cross-Cutting Requirements

### Config-driven (applies to every section above)
- Every group, page, panel, and sub-feature is independently toggleable in `config.yaml`.
- Absent = hidden completely: no route registered, no menu item, no panel.
- Backend enforces access server-side; UI hiding is never the security boundary.
- Frontend builds entirely from `GET /api/config/features`.

### HAL & Simulation
- One HAL interface per hardware domain, with real + deterministic simulation implementations.
- Simulation auto-engages when the real interface is absent, so the full dashboard runs on a dev machine.
- Responses flag `"simulated": true`; UI shows a simulation badge.

### Safety
- All state-changing actions audited (who/what/when/IP/old→new); audit log append-only.
- Destructive actions use ConfirmDialog; high-risk ones use multi-step confirmation.
- Connectivity-affecting network changes use 60-second auto-rollback.
- All filesystem access path-traversal protected (canonicalize + allowlist).

### Real-time
- Live data over WebSocket (no polling), token-authenticated handshake.
- Auto-reconnect with exponential backoff and a visible reconnecting banner.

### Roles
- Viewer (read-only), operator (hardware controls + service restart), admin (everything) — plus custom roles from User Management.
- Enforced on every endpoint regardless of UI state.

### De-duplication notes
- **Firewall** — canonical in Security; Network shows status + link.
- **Users/roles/groups** — canonical in User Management; Security and Settings reference it.
- **Cloud vs Telemetry** — separate pages, shared connection config.
- **Co-processor** — one subsystem surfaced across Hardware, OTA/FOTA, Thermal, and Logs.

---

## Platform Feature Matrix (illustrative)

| Feature | Arches | Acadia | Zion | Pinnacle | Joshua | Sequoia |
|---------|:------:|:------:|:----:|:--------:|:------:|:-------:|
| Co-processor | ✓ STM32 | ✓ Pico W | ✓ FPGA | ✓ M7/M4 | ✓ PRU | ✓ RP2040 |
| GPU panel | ✓ | — | — | — | — | optional |
| FPGA panel | — | — | ✓ | — | — | — |
| PRU panel | — | — | — | — | ✓ | — |
| Camera | ✓ | optional | optional | optional | — | optional |
| SMART storage | — | — | — | — | — | ✓ |
| Cellular | optional | optional | — | optional | — | — |
| CAN bus | optional | optional | ✓ | ✓ | ✓ | optional |

Each platform ships its own `config.yaml`; this matrix is illustrative, not prescriptive — the config is the source of truth.
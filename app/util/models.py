"""Pydantic request models for privileged/validation-prone endpoints.

These give a single source of truth for input shape, are surfaced as
422 by FastAPI's built-in validation handler (which we wrap in
``app.errors.validation_exception_handler``), and replace the
``dict[str, Any]`` body pattern on the most security-sensitive
endpoints. The existing ``app.util.validate`` helpers continue to
raise ``ApiError(400)`` for cross-field / cross-format checks that
pydantic cannot express (e.g. contiguous-netmask check).
"""

from __future__ import annotations

import ipaddress
import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator


def _validate_ipv4(v: str | None) -> str | None:
    """Parse ``v`` as an IPv4 address; raise ``ValueError`` on failure."""
    if v is None:
        return None
    try:
        ipaddress.IPv4Address(v)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid IPv4 address: {v}") from exc
    return v


# --- network --------------------------------------------------------------
class StaticNetworkConfig(BaseModel):
    """Body for ``POST /api/settings/network/{iface}``.

    For ``method == "static"`` the address/netmask/gateway/dns fields
    are required and parsed as IPv4 addresses; for ``method == "dhcp"``
    they must be omitted.
    """

    method: Literal["dhcp", "static"] = "dhcp"
    address: str | None = None
    netmask: str | None = None
    gateway: str | None = None
    dns: str | None = None
    confirm: bool = False
    # Bounded so a runaway client cannot request an indefinite rollback window.
    rollback_armed_s: int = Field(default=60, ge=5, le=600)

    @field_validator("address", "gateway", "dns")
    @classmethod
    def _must_be_ipv4(cls, v: str | None) -> str | None:
        return _validate_ipv4(v)

    @field_validator("netmask")
    @classmethod
    def _netmask_must_be_contiguous(cls, v: str | None) -> str | None:
        if v is None:
            return v
        # Accept "255.255.255.0" or "/24" — refuse non-contiguous masks.
        if v.startswith("/"):
            try:
                ipaddress.IPv4Network(f"0.0.0.0{v}", strict=False)
            except ValueError as exc:
                raise ValueError(f"Invalid netmask: {v}") from exc
            return v
        try:
            ipaddress.IPv4Network(f"0.0.0.0/{v}", strict=False)
        except ValueError as exc:
            raise ValueError(f"Invalid netmask: {v}") from exc
        return v


# --- firewall -------------------------------------------------------------
_FIREWALL_ACTION = Literal["allow", "deny"]
_PROTOCOL = Literal["tcp", "udp", "any"]


class FirewallRuleCreate(BaseModel):
    action: _FIREWALL_ACTION = "allow"
    port: int = Field(ge=1, le=65535)
    protocol: _PROTOCOL = "tcp"
    # "any" or a valid IPv4 (e.g. "192.168.1.0/24" or a bare IP)
    source: str = "any"
    destination: str = "any"
    position: int = 0
    confirm: bool = False

    @field_validator("source", "destination")
    @classmethod
    def _cidr_or_any(cls, v: str) -> str:
        if v == "any":
            return v
        try:
            # Accept "192.168.1.1" or "192.168.1.0/24"
            if "/" in v:
                ipaddress.IPv4Network(v, strict=False)
            else:
                ipaddress.IPv4Address(v)
        except ValueError as exc:
            raise ValueError(f"Invalid address/CIDR: {v}") from exc
        return v


class FirewallRuleEnable(BaseModel):
    enabled: bool = True
    confirm: bool = False


# --- ports / services -----------------------------------------------------
class PortClose(BaseModel):
    confirm: bool = False
    protocol: _PROTOCOL = "tcp"


class ServiceAction(BaseModel):
    """Body for ``POST /api/sysadmin/services/{name}/{action}``.

    ``confirm`` is required for stop/disable/restart — enforced by the
    router regardless of body.
    """

    confirm: bool = False


# --- settings (reboot / shutdown / factory reset) -------------------------
class RebootBody(BaseModel):
    confirm: bool = False
    now: bool = True


class ShutdownBody(BaseModel):
    confirm: bool = False


# --- network link up/down -------------------------------------------------
class NetworkLink(BaseModel):
    up: bool = True
    confirm: bool = False


# --- ota ------------------------------------------------------------------
class OtaSlotSwitch(BaseModel):
    slot: Literal["A", "B"]
    confirm: bool = False


class OtaRollback(BaseModel):
    confirm: bool = False


# --- alerts ----------------------------------------------------------------
class AlertSnooze(BaseModel):
    """Body for ``POST /api/cloud/alerts/{id}/snooze``.

    Snooze is bounded to 1 hour..1 week (#89). Unbounded snoozes (e.g.
    ``hours=10**9``) were previously legal and would silently disable an
    alert for ~114,000 years; the cap keeps the audit row meaningful
    and the operator's "this will fire again in N hours" mental model
    intact.
    """

    hours: int = Field(default=1, ge=1, le=168)


# --- hostname confirmation helpers ----------------------------------------
# RFC-1123: each label is 1..63 chars [A-Za-z0-9-], not starting or ending
# with '-'; the full hostname (concatenation) is up to 253 chars.
_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def is_valid_hostname(v: str) -> bool:
    """RFC-1123-ish hostname check (used by factory-reset confirm_hostname).

    Accepts ``host``, ``host-1``, ``my-host.example``, and rejects empty
    strings, leading/trailing hyphens, and labels > 63 chars.
    """
    if not v or len(v) > 253:
        return False
    return all(_LABEL_RE.match(label) for label in v.split("."))


# Re-export commonly used field types so routers can import from one place.
PositivePort = Annotated[int, Field(ge=1, le=65535)]


# --- hardware (Modbus) -----------------------------------------------------
class _StrictModel(BaseModel):
    model_config = {"extra": "forbid"}


class OtaConfigPush(_StrictModel):
    """Body for ``POST /api/ota/config`` (#87, audit §8 / Guardrail 1).

    The previous shape was a raw ``dict[str, Any]`` — anything pushed
    landed in ``restore_points`` unvalidated. This model rejects
    unknown keys (``extra="forbid"`` via ``_StrictModel``), pins the
    version to a ``X.Y`` semver fragment, and constrains ``kind`` to
    the four known sections. ``payload`` is intentionally
    ``dict[str, Any]`` — the per-kind sub-schema lives in the
    interface/media HAL boundary; here we accept any well-formed JSON
    object and let the downstream task reject known-bad shapes.
    """

    kind: Literal["device", "interface", "system", "media"]
    version: str = Field(min_length=3, max_length=16, pattern=r"^\d+\.\d+$")
    payload: dict[str, Any] = Field(default_factory=dict)
    name: str | None = Field(default=None, max_length=128)


# --- bluetooth -------------------------------------------------------------
class BluetoothToggle(_StrictModel):
    """Body for ``POST /api/sysadmin/bluetooth/enable`` (#122).

    Toggling the radio can lock out a Bluetooth-only headless flow, so
    ``confirm: true`` is required. ``extra="forbid"`` rejects typos in
    other keys.
    """

    enabled: bool = True
    confirm: bool = False


# --- Wi-Fi profiles (#110, audit §2) ---------------------------------------
# audit.md §2 Networking: "Wi-Fi (SSID/creds/country)." The router used
# to accept only ``ssid`` and forget the connection. This model carries
# the full profile (PSK, country, EAP, identity) and rejects unknown
# keys so a typo (e.g. ``passphrase`` vs ``psk``) doesn't silently land
# in the DB.

_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")  # ISO 3166-1 alpha-2


class WifiConnectProfile(_StrictModel):
    """Body for ``POST /api/sysadmin/wifi/connect`` (#110).

    ``country`` is validated as ISO 3166-1 alpha-2 (two uppercase
    letters); ``eap`` accepts a small enum of common 802.1X methods
    and is None for open / WPA2-Personal. ``psk`` is the WPA2-Personal
    pre-shared key (8..63 ASCII chars per WPA2 spec) and is None for
    open / EAP networks. ``identity`` is the EAP outer identity.
    """

    ssid: str = Field(min_length=1, max_length=32)
    psk: str | None = Field(default=None, min_length=8, max_length=63)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    eap: Literal["PEAP", "TTLS", "TLS"] | None = None
    identity: str | None = Field(default=None, max_length=253)
    # Wi-Fi connect is lockout-capable (associating with a wrong SSID
    # on the only radio can leave the host unreachable). req.md §7
    # mandates explicit confirm for every lockout-capable action
    # (#142). The router enforces ``confirm: true`` via require_confirm
    # regardless of the body, mirroring the firewall/service endpoints.
    confirm: bool = False

    @field_validator("country")
    @classmethod
    def _country_must_be_alpha2(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _COUNTRY_RE.match(v):
            raise ValueError(f"Invalid country code: {v!r} (expected ISO 3166-1 alpha-2)")
        return v


class ModbusWrite(_StrictModel):
    """Body for ``POST /api/hardware/modbus/write``.

    Bounds match the corresponding ``GET /api/hardware/modbus`` Query
    constraints (#88). The previous body was a raw ``dict[str, int]`` with
    no validation — an out-of-range address (``999999``) was silently
    audited as "written" even though the device may have rejected it.
    """

    unit_id: int = Field(ge=0, le=247)
    address: int = Field(ge=0, le=65535)
    count: int = Field(ge=1, le=125)
    value: int


class MqttPublish(_StrictModel):
    """Body for ``POST /api/hardware/mqtt/publish`` (#96).

    ``payload`` is capped at 64 KiB — matches typical MQTT broker message
    caps and prevents a 1 GB string from being held in memory by
    FastAPI's body parser.
    """

    topic: str = Field(min_length=1, max_length=256)
    payload: str = Field(max_length=64 * 1024)


# --- SSH policy (#95) ------------------------------------------------------
# sshd_config is a small set of well-known keys. ``extra="forbid"`` keeps
# typos from being silently accepted (e.g. ``permit_root_login`` vs the
# sshd spelling ``PermitRootLogin`` would otherwise round-trip in
# persisted state without the operator noticing).


_SSH_PERMIT_ROOT = Literal["yes", "no", "prohibit-password", "forced-commands-only"]
_SSH_PASSWORD_AUTH = Literal["yes", "no"]


class SshPolicyUpdate(_StrictModel):
    """Body for ``PUT /api/security/ssh-policy`` (#95).

    Each field is optional — the operator may override one key without
    resending the full policy. ``confirm`` is required by the router
    (privileged write).
    """

    permit_root_login: _SSH_PERMIT_ROOT | None = None
    password_authentication: _SSH_PASSWORD_AUTH | None = None
    kbd_interactive_authentication: _SSH_PASSWORD_AUTH | None = None
    x11_forwarding: bool | None = None
    port: Annotated[int, Field(ge=1, le=65535)] | None = None
    allow_users: str | None = None
    confirm: bool = False


# --- per-kind interface config schemas -------------------------------------
# Each interface kind only accepts the fields it actually understands.
# `extra="forbid"` makes pydantic 422 on unknown keys, so a typo or a
# field from a different kind doesn't silently land in the DB (#51).


class UartConfig(_StrictModel):
    device: str
    baud: int = Field(ge=1, le=10_000_000)
    bits: Literal[5, 6, 7, 8] = 8
    parity: Literal["none", "odd", "even"] = "none"
    stop: Literal[1, 2] = 1


class Rs485Config(_StrictModel):
    device: str
    baud: int = Field(ge=1, le=10_000_000)
    termination: bool = True
    de_gpio: int = Field(ge=0, le=999)


class Rs422Config(_StrictModel):
    device: str
    baud: int = Field(ge=1, le=10_000_000)
    duplex: Literal["full", "half"] = "full"


class I2cConfig(_StrictModel):
    bus: int = Field(ge=0, le=16)
    baud: int = Field(ge=1_000, le=10_000_000)


class SpiConfig(_StrictModel):
    bus: int = Field(ge=0, le=16)
    mode: Literal[0, 1, 2, 3] = 0
    speed_hz: int = Field(ge=1_000, le=100_000_000)


class CanConfig(_StrictModel):
    bitrate: int = Field(ge=10_000, le=5_000_000)


class CanFdConfig(_StrictModel):
    nom_bitrate: int = Field(ge=10_000, le=5_000_000)
    data_bitrate: int = Field(ge=10_000, le=5_000_000)


class ModbusConfig(_StrictModel):
    mode: Literal["rtu", "tcp"] = "rtu"
    unit: int = Field(ge=1, le=247)
    baud: int = Field(ge=1, le=10_000_000)


class OneWireConfig(_StrictModel):
    gpio: int = Field(ge=0, le=999)


class GpioConfig(_StrictModel):
    banks: int = Field(ge=1, le=8)
    pins: int = Field(ge=1, le=512)


# Map interface kind → pydantic schema. Used by the router to validate
# configure() bodies (#51). Kind names match `app.hal.interfaces.DEFAULTS`.
_INTERFACE_CONFIG_MODELS = {
    "uart": UartConfig,
    "rs485": Rs485Config,
    "rs422": Rs422Config,
    "i2c": I2cConfig,
    "spi": SpiConfig,
    "can": CanConfig,
    "canfd": CanFdConfig,
    "modbus": ModbusConfig,
    "onewire": OneWireConfig,
    "gpio": GpioConfig,
}


def interface_config_model(kind: str) -> Any:
    """Return the pydantic model for an interface kind, or None if unknown."""
    return _INTERFACE_CONFIG_MODELS.get(kind)


# --- u-boot env (#131, audit §4) -------------------------------------------
# audit.md Guardrail 1: validate before writing. U-Boot env keys are a
# constrained alphabet — a typo silently overwrites nothing in
# simulation but would brick the device in production. The pattern
# ``^[A-Za-z][A-Za-z0-9_]*$`` matches the legal set U-Boot accepts
# (letters, digits, underscores; must start with a letter). The
# allow-list is a deliberate second gate: a syntactically valid key
# like ``foo_bar`` is still rejected so a typo near a real key
# (``boot_partion`` vs ``boot_partition``) can't land in the env.
_UBOOT_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_UBOOT_KNOWN_KEYS: frozenset[str] = frozenset(
    {
        "boot_partition",
        "boot_count",
        "upgrade_available",
        "bootdelay",
        "fdt_addr_r",
        "kernel_addr_r",
        "ramdisk_addr_r",
        "bootcmd",
        "baudrate",
        "boot_order",
    }
)


class UbootEnvSet(_StrictModel):
    """Body for ``POST /api/overview/boot/uboot-env`` (#131, #151).

    ``key`` is constrained to the U-Boot env alphabet AND a known
    allow-list (the allow-list is the second gate: a syntactically
    valid key like ``foo_bar`` still 422s so a typo near a real key
    cannot silently land in the env). ``value`` is bounded to 256
    chars. ``extra="forbid"`` (via ``_StrictModel``) rejects any
    unexpected field.

    Privileged write — ``confirm: true`` is enforced by the router
    (mirror of every other boot write). The ``confirm`` field is
    present on the model so pydantic accepts the operator's standard
    confirm payload.

    ``rollback_armed_s`` (1..3600, default 60) is the watchdog window
    after which the plan auto-rolls back if the device never
    re-heartbeats (#151).
    """

    key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    value: str = Field(max_length=256)
    confirm: bool = False
    rollback_armed_s: int = Field(default=60, ge=1, le=3600)

    @field_validator("key")
    @classmethod
    def _key_must_be_allow_listed(cls, v: str) -> str:
        if v not in _UBOOT_KNOWN_KEYS:
            raise ValueError(f"Unknown u-boot env key '{v}'; allowed: {sorted(_UBOOT_KNOWN_KEYS)}")
        return v


# --- localization (#144, audit §1) ----------------------------------------
# BCP-47-ish locale code: language (2-3 lowercase), optional region
# (`_` + 2 uppercase), optional script (`.` + 1* [A-Za-z0-9-]).
# Examples that pass: "en", "en_US", "zh_Hans", "zh_Hans_CN",
# "pt_BR", "de_AT". A 5-letter language ("en_USx") is rejected by the
# region length bound.
_LOCALE_RE = re.compile(r"^[a-z]{2,3}(_[A-Z]{2})?(\.[A-Za-z0-9-]+)?$")

# Locales the SPA / translator bundles ship translations for. The
# operator's selected locale (PUT) does not have to be in this set —
# e.g. an operator may pick `de_AT` before the team has shipped an
# Austrian translation. The known set is what the i18n loader will
# use as a fallback chain. Adding to this list is the SPA's job, not
# the API's.
# Public re-export — ``app.routers.settings`` needs this for the
# GET /api/settings/localization response. Name is public-by-design.
KNOWN_LOCALES: frozenset[str] = frozenset({"en", "de", "zh", "fr", "es", "ja", "pt", "pt_BR"})


class LocaleUpdate(_StrictModel):
    """Body for ``PUT /api/settings/localization`` (#144, audit §1).

    ``locale`` is constrained by:
      * a regex (``_LOCALE_RE``) — rejects ``..``, upper-case language,
        whitespace, path traversal, ``%xx`` escapes, etc.
      * the value the operator types is what the dashboard shows.
        We do not silently coerce ``EN_us`` → ``en_US`` — better to
        surface the mismatch.

    The router persists the locale in ``system_settings_kv`` (per
    ``#42``). It is NOT a privileged write — locale only affects
    UI strings, not device behavior — so no ``confirm`` field.
    """

    locale: str = Field(min_length=2, max_length=32, pattern=_LOCALE_RE.pattern)


# --- UI prefs (Features.md Settings: Dashboard Config) ---------------------
# Operator-chosen SPA behavior: which page the SPA boots to, how many
# rows a table shows by default, and how often the live WebSocket
# metrics refresh the dashboard tiles. All optional so the SPA can
# PUT a partial update (only the keys it wants to change).
class UiPrefsUpdate(_StrictModel):
    """Body for ``PUT /api/settings/ui-prefs`` (Features.md Settings).

    Persists operator-chosen SPA behavior to ``system_settings_kv``.
    NOT a privileged device write — these only affect the local UI,
    not device state — so no ``confirm`` field. Each field is optional
    so a partial update doesn't reset the others.
    """

    landing_page: str | None = Field(default=None, max_length=64, pattern=r"^/[a-z0-9_/\-]*$")
    items_per_page: int | None = Field(default=None, ge=5, le=200)
    refresh_interval_s: int | None = Field(default=None, ge=1, le=600)


# --- Appearance (Features.md Settings: Appearance) -------------------------
# Operator-chosen look-and-feel for the SPA. The values are persisted
# to ``system_settings_kv`` (one row per key) so they round-trip through
# backup/restore and the SPA can read them on mount. ``auto`` theme
# follows the browser/OS preference; ``light`` / ``dark`` are explicit
# overrides. Accent is a CSS color (validated loosely so the operator
# can use #hex / rgb() / hsl() / oklch() without a 422 from us).
class AppearanceUpdate(_StrictModel):
    """Body for ``PUT /api/settings/appearance`` (Features.md Settings).

    Each field is optional so a partial PUT doesn't reset the others.
    None of these are privileged device writes — appearance is a UI
    concern, not device state — so no ``confirm`` and no apply_state.
    """

    theme: Literal["auto", "light", "dark"] | None = None
    accent_color: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^(#[0-9a-fA-F]{3,8}|rgb\([^()]*\)|rgba\([^()]*\)|hsl\([^()]*\)|hsla\([^()]*\)|oklch\([^()]*\)|oklab\([^()]*\))$",
    )
    date_format: Literal["iso", "us", "eu", "iso-time"] | None = None
    time_format: Literal["24h", "12h"] | None = None
    units: Literal["metric", "imperial"] | None = None
    density: Literal["comfortable", "compact", "spacious"] | None = None
    sidebar_default: Literal["expanded", "collapsed"] | None = None


# --- OTA channel / server config (#147, audit §8) -------------------------
# A simple http(s) URL validator — pydantic's HttpUrl is a validator
# factory, not a type, and using it as `field: HttpUrl` fails in v2
# (CLAUDE.md Don't: never use pydantic.* validators as type annotations).
# A regex covers the cases we actually care about: scheme + host + path.
_OTA_BASE_URL_RE = re.compile(r"^https?://[A-Za-z0-9._\-]+(:\d{1,5})?(/[\w./\-]*)?$")


class OtaChannelUpdate(_StrictModel):
    """Body for ``PUT /api/ota/channel`` (#147, audit §8).

    Persists the operator-chosen update channel + SWUpdate server URL
    so the auto-check loop and the next install can use the new
    source. ``channel`` is constrained to ``stable | beta | nightly``
    so an upstream typo can't silently switch to an unvetted feed.
    ``base_url`` is bounded to a http(s) URL.

    All four fields are required — the operator is explicitly
    re-stating the whole server config (per audit §8 "update
    channel/server config"). ``extra="forbid"`` (via ``_StrictModel``)
    rejects unknown keys.
    """

    base_url: str = Field(min_length=8, max_length=512)
    channel: Literal["stable", "beta", "nightly"]
    auto_check: bool
    signing_key_id: str = Field(min_length=1, max_length=128)
    confirm: bool = False

    @field_validator("base_url")
    @classmethod
    def _base_url_must_be_http(cls, v: str) -> str:
        if not _OTA_BASE_URL_RE.match(v):
            raise ValueError("base_url must be an http(s) URL like https://updates.example.com")
        return v

    @field_validator("signing_key_id")
    @classmethod
    def _signing_key_id_pattern(cls, v: str) -> str:
        if not re.match(r"^[A-Za-z0-9_\-]+$", v):
            raise ValueError("signing_key_id must match [A-Za-z0-9_-]+")
        return v


# --- OTA staged rollout (#146, audit §8) -----------------------------------
# The rollout pipeline progresses 1% → 10% → 100%. Each stage is a
# row in ``rollout_stages`` (see migration 14). The pydantic model
# constrains the input to a valid subset of stages (always 1, 10,
# 100 — never a typo like "11" or "all").
_ROLLOUT_ALLOWED_PERCENTS: tuple[int, ...] = (1, 10, 100)


class RolloutStart(_StrictModel):
    """Body for ``POST /api/ota/rollout/start`` (#146, audit §8).

    ``update_id`` is an operator-chosen label (typically the OTA
    history id or a human-readable version string) so multiple
    rollouts can be tracked concurrently. ``stages`` is the cohort
    percentages to walk through — defaults to ``[1, 10, 100]`` and
    rejects any other value (the validator catches typos before they
    reach the database).

    ``extra="forbid"`` (via ``_StrictModel``) rejects unknown keys.
    ``confirm`` is the operator's standard confirm payload — required
    server-side via ``require_confirm`` (privileged write).
    """

    update_id: str = Field(min_length=1, max_length=128)
    stages: list[Literal[1, 10, 100]] = Field(default_factory=lambda: list(_ROLLOUT_ALLOWED_PERCENTS))  # type: ignore[arg-type]
    confirm: bool = False

    @field_validator("stages")
    @classmethod
    def _stages_must_be_unique_and_strictly_ordered(cls, v: list[Literal[1, 10, 100]]) -> list[Literal[1, 10, 100]]:
        if not v:
            raise ValueError("stages must be non-empty")
        # Each percent must be in the allowed set (Literal already
        # gates the type) and the stages must be unique + strictly
        # ascending so the state machine can only walk forward.
        if sorted(v) != list(v):
            raise ValueError("stages must be strictly ascending (e.g. [1, 10, 100])")
        if len(set(v)) != len(v):
            raise ValueError("stages must not contain duplicates")
        for pct in v:
            if pct not in _ROLLOUT_ALLOWED_PERCENTS:
                raise ValueError(f"stage percent {pct} is not allowed; must be one of {_ROLLOUT_ALLOWED_PERCENTS}")
        return v


# --- thermal watchdog config (#129, audit §1) ------------------------------
# audit.md §1: "watchdog config" (timeout, kick interval). Each field
# is bounded: a 0-second kick would defeat the watchdog; a 1-hour
# timeout would never fire before the next reboot. The 1..600 window
# matches the rollback-armed window used elsewhere in the config
# layer so the operator's mental model is consistent.


class WatchdogConfigUpdate(_StrictModel):
    """Body for ``PUT /api/thermal/watchdog`` (#129).

    All three fields are bounded. ``extra="forbid"`` (via
    ``_StrictModel``) rejects typos. Privileged write — ``confirm``
    is enforced by the router.
    """

    timeout_s: int = Field(ge=1, le=600)
    kick_interval_s: int = Field(ge=1, le=600)
    nowayout: bool = False
    confirm: bool = False


# --- log retention (#125, audit §6) ----------------------------------------
# audit.md §6: "disk usage + retention/vacuum, persistent vs
# volatile state." The retention job prunes ``metric_history`` and
# ``telemetry_queue`` past ``history_retention_days``. Bounds:
# - ``history_retention_days`` >= 1 day (anything less would
#   delete every metric row before the operator sees it) and
#   <= 3650 (10 years — anything longer is almost certainly a typo).
# - ``vacuum_interval_hours`` >= 1 hour and <= 168 (1 week — once
#   per week is the practical floor for a SQLite VACUUM job).


class LogRetentionUpdate(_StrictModel):
    """Body for ``PUT /api/files/logs/retention`` (#125).

    Both fields are bounded. ``extra="forbid"`` (via
    ``_StrictModel``) rejects typos. Privileged write —
    ``confirm`` is enforced by the router.
    """

    history_retention_days: int = Field(ge=1, le=3650)
    vacuum_interval_hours: int = Field(ge=1, le=168)
    confirm: bool = False


class LogVacuumBody(_StrictModel):
    """Body for ``POST /api/files/logs/vacuum`` (#125)."""

    confirm: bool = False


class OtaEndpointUpdate(_StrictModel):
    """Body for ``PUT /api/settings/system/ota-endpoint`` (audit §1, §8, #124).

    ``endpoint`` is the OTA update server URL. The canonical write path
    also mirrors to ``system_settings_kv`` so the value survives a
    factory reset of the ``provisioning`` row. ``extra="forbid"`` (via
    ``_StrictModel``) rejects typos. Privileged write — ``confirm`` is
    enforced by the router.
    """

    endpoint: str = Field(min_length=1, max_length=2048)
    confirm: bool = False

    @field_validator("endpoint")
    @classmethod
    def _endpoint_must_be_http(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("endpoint must be an http(s):// URL")
        return v


# --- OTA signature (audit §8, #120) ---------------------------------------
# audit §8 OTA: "signed update chain." The previous implementation
# literally emitted "checking signature" (app/tasks/ota.py:155) without
# any actual swupdate / GPG verification — a malicious or corrupted
# bundle would be accepted and "installed" in simulation; in real
# mode this would be a critical security gap.
#
# The fix: each /api/ota/app upload must carry an ``OtaSignature``.
# The router looks up the public key by ``key_id`` in the
# ``signing_keys`` table, verifies the ed25519 / rsa-pss signature
# over ``signed_payload``, AND verifies that ``signed_payload``
# contains the SHA256 of the uploaded bundle (so a signed manifest
# for bundle A cannot be replayed against bundle B). A failure at
# either step is a 422 with the audit row recorded.
#
# The ``signed_payload`` is a small JSON blob:
#   {"version": "<bundle version>", "sha256": "<hex sha256>"}
# so the signer only commits to a manifest-shaped string, not the
# full bundle (which is much larger and the signature would be
# over a multi-MiB payload).


class OtaSignature(_StrictModel):
    """Body for ``POST /api/ota/app`` (audit §8, #120).

    ``alg`` selects the verification path; ``signature`` is
    base64-encoded raw signature bytes over the UTF-8 JSON encoding
    of ``signed_payload`` (a small dict, not the full bundle).
    ``public_key_id`` indexes ``signing_keys.key_id``. ``extra="forbid"``
    rejects typos so a misspelled ``public_keyid`` doesn't bypass
    verification.
    """

    alg: Literal["ed25519", "rsa-pss"]
    signature: str = Field(min_length=1, max_length=4096)
    public_key_id: str = Field(min_length=1, max_length=64)
    signed_payload: str = Field(min_length=1, max_length=4096)

    @field_validator("signed_payload")
    @classmethod
    def _payload_must_be_manifest_json(cls, v: str) -> str:
        """``signed_payload`` must parse as a JSON object containing
        ``version`` and ``sha256`` keys (#120). This is the same
        shape the signer signs; rejecting malformed input here
        surfaces a typo before the (expensive) verification call."""
        try:
            obj = json.loads(v)
        except json.JSONDecodeError as exc:
            raise ValueError(f"signed_payload must be valid JSON: {exc}") from exc
        if not isinstance(obj, dict):
            raise ValueError("signed_payload must be a JSON object")
        if not isinstance(obj.get("version"), str) or not obj["version"]:
            raise ValueError("signed_payload.version must be a non-empty string")
        if not isinstance(obj.get("sha256"), str) or not obj["sha256"]:
            raise ValueError("signed_payload.sha256 must be a non-empty string")
        return v


class SigningKeyUpload(_StrictModel):
    """Body for ``POST /api/security/signing-keys`` (#120).

    ``public_key`` for ``alg=="ed25519"`` is base64 of 32 raw bytes;
    for ``alg=="rsa-pss"`` it is a PEM-encoded SubjectPublicKeyInfo
    (also base64-able but we accept PEM text directly).
    """

    key_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    alg: Literal["ed25519", "rsa-pss"]
    public_key: str = Field(min_length=1, max_length=8192)
    label: str | None = Field(default=None, max_length=128)

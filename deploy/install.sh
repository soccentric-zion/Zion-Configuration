#!/usr/bin/env bash
#
# install.sh — install the dashboard as a systemd service on a Linux device.
#
# Idempotent: safe to re-run to upgrade. Creates an unprivileged service user,
# a Python virtualenv with runtime dependencies, installs the config and unit,
# then enables and starts the service.
#
# Usage (as root):  sudo deploy/install.sh

set -euo pipefail

APP_DIR=/opt/device-dashboard
CFG_DIR=/etc/device-dashboard
SERVICE_USER=dashboard
UNIT=dashboard.service
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
	echo "This installer must run as root (try: sudo $0)" >&2
	exit 1
fi

echo "==> Creating service account '$SERVICE_USER'"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
	useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

echo "==> Installing application to $APP_DIR"
mkdir -p "$APP_DIR" "$APP_DIR/data" "$CFG_DIR"
# Copy app code, self-hosted frontend assets, compliance data (CVE / SBOM /
# license reporting), and the example config + requirements doc
# (operators consult these at the device). Missing these silently
# degrades the Security panel to placeholder data (#52).
cp -r \
	"$SRC_DIR/app" \
	"$SRC_DIR/static" \
	"$SRC_DIR/compliance" \
	"$SRC_DIR/requirements.txt" \
	"$SRC_DIR/config.example.yaml" \
	"$SRC_DIR/config.yaml.example" \
	"$SRC_DIR/req.md" \
	"$APP_DIR/"

echo "==> Verifying critical files are present"
for f in compliance/compliance.yaml compliance/sbom.json config.example.yaml; do
	if [[ ! -f "$APP_DIR/$f" ]]; then
		echo "WARNING: $APP_DIR/$f missing after install — security reporting will be degraded" >&2
	fi
done

echo "==> Creating virtualenv and installing runtime dependencies"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> Installing configuration (preserving any existing config.yaml)"
if [[ ! -f "$CFG_DIR/config.yaml" ]]; then
	cp "$SRC_DIR/config.example.yaml" "$CFG_DIR/config.yaml"
	# Generate a persistent random JWT secret so sessions survive restarts.
	secret="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
	sed -i "s|change-me-to-a-long-random-string|${secret}|" "$CFG_DIR/config.yaml"
	echo "    wrote $CFG_DIR/config.yaml (default admin/admin — change on first login)"
fi

echo "==> Setting ownership"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR" "$CFG_DIR"
chmod 750 "$CFG_DIR"
chmod 640 "$CFG_DIR/config.yaml"

echo "==> Installing systemd unit"
install -m 644 "$SRC_DIR/deploy/$UNIT" "/etc/systemd/system/$UNIT"
systemctl daemon-reload
systemctl enable "$UNIT"
systemctl restart "$UNIT"

echo
echo "Done. Dashboard is starting on http://<device>:8000"
echo "  status:  systemctl status $UNIT"
echo "  logs:    journalctl -u $UNIT -f"

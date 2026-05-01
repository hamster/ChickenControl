#!/bin/sh
# install.sh — deploy ChickenControl to a Raspberry Pi
#
# Must be run as root on the Pi:
#   sudo sh install.sh
#
# What this does:
#   1. Installs Python dependencies
#   2. Creates runtime directories
#   3. Installs chickendoor CLI and chickenctl daemon to /usr/local/bin/
#   4. Installs doors.conf.example to /etc/chickendoor/ if no config exists
#   5. Generates a self-signed TLS certificate if none exists
#   6. Installs and enables the startup script (systemd preferred, init.d fallback)

set -e

CONF_DIR=/etc/chickendoor
STATE_DIR=/var/lib/chickendoor
BIN_DIR=/usr/local/bin
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ---------------------------------------------------------------------------
check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "error: must be run as root" >&2
        exit 1
    fi
}

install_deps() {
    echo "==> Installing Python dependencies..."
    # --break-system-packages is required on Bookworm/Trixie (PEP 668).
    pip3 install --quiet --break-system-packages flask waitress rpi-lgpio
}

create_dirs() {
    echo "==> Creating directories..."
    install -d -m 755 "$CONF_DIR"
    install -d -m 750 "$STATE_DIR"
}

install_binaries() {
    echo "==> Installing binaries..."
    install -m 755 "$SCRIPT_DIR/door_control.py" "$BIN_DIR/door_control.py"
    install -m 755 "$SCRIPT_DIR/chickendoor.py"  "$BIN_DIR/chickendoor"
    install -m 755 "$SCRIPT_DIR/chickenctl.py"   "$BIN_DIR/chickenctl"

    # Ensure door_control is importable from the same directory as the scripts.
    # Python will find it since /usr/local/bin is typically in sys.path for
    # scripts; if not, create a symlink in site-packages.
    SITE_PACKAGES="$(python3 -c 'import site; print(site.getsitepackages()[0])')"
    if [ ! -f "$SITE_PACKAGES/door_control.py" ]; then
        ln -sf "$BIN_DIR/door_control.py" "$SITE_PACKAGES/door_control.py"
    fi
}

install_config() {
    if [ -f "$CONF_DIR/doors.conf" ]; then
        echo "==> $CONF_DIR/doors.conf already exists — skipping"
    else
        echo "==> Installing example config to $CONF_DIR/doors.conf"
        install -m 640 "$SCRIPT_DIR/doors.conf.example" "$CONF_DIR/doors.conf"
        echo ""
        echo "    IMPORTANT: Edit $CONF_DIR/doors.conf before starting the daemon."
        echo "    At minimum set: relay_a, relay_b pins and a unique token."
    fi
    install -m 640 "$SCRIPT_DIR/doors.conf.example" "$CONF_DIR/doors.conf.example"
}

generate_cert() {
    if [ -f "$CONF_DIR/server.crt" ]; then
        echo "==> TLS certificate already exists — skipping"
        return
    fi
    echo "==> Generating self-signed TLS certificate (10-year)..."
    openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout "$CONF_DIR/server.key" \
        -out    "$CONF_DIR/server.crt" \
        -days 3650 \
        -subj "/CN=chickencoop.local" \
        2>/dev/null
    chmod 600 "$CONF_DIR/server.key"
    chmod 644 "$CONF_DIR/server.crt"
    echo "    Certificate: $CONF_DIR/server.crt"
    echo "    Private key: $CONF_DIR/server.key"
    echo ""
    echo "    Copy server.crt to Home Assistant so the integration can verify it."
}

install_service() {
    if command -v systemctl > /dev/null 2>&1 && \
       [ -d /run/systemd/system ]; then
        echo "==> Installing systemd service..."
        install -m 644 "$SCRIPT_DIR/chickenctl.service" \
            /etc/systemd/system/chickenctl.service
        systemctl daemon-reload
        systemctl enable chickenctl
        echo "    Enabled.  Start with: systemctl start chickenctl"
    else
        echo "==> Installing SysV init script..."
        install -m 755 "$SCRIPT_DIR/chickenctl.init" /etc/init.d/chickenctl
        update-rc.d chickenctl defaults
        echo "    Enabled.  Start with: service chickenctl start"
    fi
}

# ---------------------------------------------------------------------------
check_root
install_deps
create_dirs
install_binaries
install_config
generate_cert
install_service

echo ""
echo "==> Installation complete."
echo ""
echo "Next steps:"
echo "  1. Edit $CONF_DIR/doors.conf"
echo "     - Set correct relay_a / relay_b BCM pin numbers"
echo "     - Set a unique token (python3 -c \"import secrets; print(secrets.token_urlsafe(32))\")"
echo "  2. Start the daemon:"
if command -v systemctl > /dev/null 2>&1 && [ -d /run/systemd/system ]; then
echo "       systemctl start chickenctl"
echo "       journalctl -u chickenctl -f"
else
echo "       service chickenctl start"
echo "       tail -f /var/log/chickenctl.log"
fi
echo "  3. Test:"
echo "       TOKEN=\$(grep ^token $CONF_DIR/doors.conf | cut -d= -f2 | tr -d ' ')"
echo "       curl -k -H \"Authorization: Bearer \$TOKEN\" https://localhost:8443/door/coop/status"
echo "  4. Install $CONF_DIR/server.crt on Home Assistant"
echo "     (or enable verify_ssl: false in the HA integration config)"

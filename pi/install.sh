#!/bin/sh
# install.sh — deploy ChickenControl to a Raspberry Pi
#
# Must be run as root on the Pi:
#   sudo sh install.sh
#
# What this does:
#   1. Installs Python dependencies
#   2. Creates service users, groups, and runtime directories
#   3. Installs binaries to /usr/local/bin/
#   4. Installs doors.conf.example to /etc/chickendoor/ if no config exists
#   5. Generates a self-signed TLS certificate if none exists
#   6. Installs and enables the startup scripts (systemd preferred, SysV fallback)
#
# Service architecture:
#   chickengate  — hardware daemon (GPIO + scheduler), runs as chickenhw
#   chickenctl   — HTTPS proxy for Home Assistant, runs as chickennet

set -e

CONF_DIR=/etc/chickendoor
STATE_DIR=/var/lib/chickendoor
BIN_DIR=/usr/local/bin
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

HW_USER=chickenhw
NET_USER=chickennet
IPC_GROUP=chicken-ipc

# ---------------------------------------------------------------------------
check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "error: must be run as root" >&2
        exit 1
    fi
}

install_deps() {
    echo "==> Installing Python dependencies..."
    pip3 install --quiet --break-system-packages flask waitress astral rpi-lgpio
}

create_users() {
    echo "==> Creating service users and groups..."

    # Shared IPC group — members can connect to the chickengate socket
    if ! getent group "$IPC_GROUP" > /dev/null 2>&1; then
        groupadd --system "$IPC_GROUP"
        echo "    Created group: $IPC_GROUP"
    fi

    # Hardware user: gpio group for relay control, ipc group for socket ownership
    if ! getent passwd "$HW_USER" > /dev/null 2>&1; then
        useradd --system --no-create-home --shell /usr/sbin/nologin \
            --groups gpio,"$IPC_GROUP" "$HW_USER"
        echo "    Created user: $HW_USER (groups: gpio, $IPC_GROUP)"
    fi

    # Network user: ipc group only — no GPIO access
    if ! getent passwd "$NET_USER" > /dev/null 2>&1; then
        useradd --system --no-create-home --shell /usr/sbin/nologin \
            --groups "$IPC_GROUP" "$NET_USER"
        echo "    Created user: $NET_USER (groups: $IPC_GROUP)"
    fi
}

create_dirs() {
    echo "==> Creating directories..."
    install -d -m 755 "$CONF_DIR"
    install -d -m 750 -o "$HW_USER" -g "$IPC_GROUP" "$STATE_DIR"
}

install_binaries() {
    echo "==> Installing binaries..."
    install -m 755 "$SCRIPT_DIR/door_control.py" "$BIN_DIR/door_control.py"
    install -m 755 "$SCRIPT_DIR/chickengate.py"  "$BIN_DIR/chickengate"
    install -m 755 "$SCRIPT_DIR/chickenctl.py"   "$BIN_DIR/chickenctl"
    install -m 755 "$SCRIPT_DIR/chickendoor.py"  "$BIN_DIR/chickendoor"

    # Ensure door_control is importable alongside the installed scripts.
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
        install -m 640 -o root -g "$IPC_GROUP" \
            "$SCRIPT_DIR/doors.conf.example" "$CONF_DIR/doors.conf"
        echo ""
        echo "    IMPORTANT: Edit $CONF_DIR/doors.conf before starting the daemons."
        echo "    At minimum set: relay pins, a unique token, and your location."
    fi
    install -m 640 -o root -g "$IPC_GROUP" \
        "$SCRIPT_DIR/doors.conf.example" "$CONF_DIR/doors.conf.example"
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
    # Both service users need to read the TLS files
    chown root:"$IPC_GROUP" "$CONF_DIR/server.key" "$CONF_DIR/server.crt"
    chmod 640 "$CONF_DIR/server.key"
    chmod 644 "$CONF_DIR/server.crt"
    echo "    Certificate: $CONF_DIR/server.crt"
    echo "    Private key: $CONF_DIR/server.key"
}

install_service() {
    if command -v systemctl > /dev/null 2>&1 && \
       [ -d /run/systemd/system ]; then
        echo "==> Installing systemd services..."
        install -m 644 "$SCRIPT_DIR/chickengate.service" \
            /etc/systemd/system/chickengate.service
        install -m 644 "$SCRIPT_DIR/chickenctl.service" \
            /etc/systemd/system/chickenctl.service
        systemctl daemon-reload
        systemctl enable chickengate chickenctl
        echo "    Enabled.  Start with: systemctl start chickengate chickenctl"
    else
        echo "==> Installing SysV init scripts..."
        install -m 755 "$SCRIPT_DIR/chickengate.init" /etc/init.d/chickengate
        install -m 755 "$SCRIPT_DIR/chickenctl.init"  /etc/init.d/chickenctl
        update-rc.d chickengate defaults
        update-rc.d chickenctl defaults
        echo "    Enabled.  Start with: service chickengate start && service chickenctl start"
    fi
}

# ---------------------------------------------------------------------------
check_root
install_deps
create_users
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
echo "     - Set correct relay_a / relay_b BCM pin numbers for each door"
echo "     - Set a unique token:"
echo "         python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
echo "     - Set latitude, longitude, and timezone in [location] for solar scheduling"
echo "  2. Start the daemons:"
if command -v systemctl > /dev/null 2>&1 && [ -d /run/systemd/system ]; then
echo "       systemctl start chickengate chickenctl"
echo "       journalctl -u chickengate -f"
echo "       journalctl -u chickenctl  -f"
else
echo "       service chickengate start"
echo "       service chickenctl  start"
echo "       tail -f /var/log/chickengate.log /var/log/chickenctl.log"
fi
echo "  3. Test:"
echo "       TOKEN=\$(grep ^token $CONF_DIR/doors.conf | cut -d= -f2 | tr -d ' ')"
echo "       curl -k -H \"Authorization: Bearer \$TOKEN\" https://localhost:8443/door/coop/status"
echo "  4. Copy $CONF_DIR/server.crt to Home Assistant"
echo "     (or disable verify_ssl in the HA integration config)"

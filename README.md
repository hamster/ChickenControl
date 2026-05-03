# ChickenControl for Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?logo=homeassistantcommunitystore&logoColor=white)][hacs-open]
[![Version](https://img.shields.io/github/v/release/hamster/ChickenControl)][releases]
[![License](https://img.shields.io/github/license/hamster/ChickenControl)](LICENSE)

Automated chicken coop door and gate control running on a Raspberry Pi, with a [Home Assistant][ha] integration for manual overrides and status monitoring.

The Pi operates independently — doors open and close on a built-in solar schedule calculated from your latitude and longitude, even when the network is down. Home Assistant adds manual open/close buttons and a live door status sensor when the Pi is reachable on the LAN.

---

## Requirements

### Hardware

- Raspberry Pi (3B or newer) with [Keyestudio 4-Channel Relay Shield][relay-hat]
- One or two linear actuators (12 V, with internal limit switches)
- 12 V power supply for the actuators

### Software — Raspberry Pi

- Raspberry Pi OS (Bullseye or newer)
- Python 3.9+, `flask`, `waitress`, `astral`, `RPi.GPIO` (or `rpi-lgpio` on Pi 5)

### Software — Home Assistant

- Home Assistant 2024.1 or newer
- [HACS][hacs] (for the easiest installation)

---

## Raspberry Pi Installation

### 1. Clone the repository

```bash
git clone https://github.com/hamster/ChickenControl.git
cd ChickenControl
```

### 2. Run the installer

```bash
sudo sh pi/install.sh
```

The installer will:

- Install Python dependencies (`flask`, `waitress`, `astral`, `RPi.GPIO` / `rpi-lgpio`)
- Create the `chickenhw` and `chickennet` system users and `chicken-ipc` group
- Create `/etc/chickendoor/` and `/var/lib/chickendoor/`
- Install all binaries to `/usr/local/bin/`
- Copy `doors.conf.example` to `/etc/chickendoor/doors.conf`
- Generate a self-signed TLS certificate in `/etc/chickendoor/`
- Install and enable both startup services (systemd preferred, SysV init fallback)

### 3. Configure doors

Edit `/etc/chickendoor/doors.conf`:

```bash
sudo nano /etc/chickendoor/doors.conf
```

At minimum, set the correct **BCM pin numbers** for your relay wiring, your **location** for solar scheduling, and generate a **unique API token**:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

A minimal working config looks like this:

```ini
[server]
host  = 0.0.0.0
port  = 8443
cert  = /etc/chickendoor/server.crt
key   = /etc/chickendoor/server.key
token = REPLACE_WITH_GENERATED_TOKEN

[location]
latitude  = 41.256
longitude = -95.995
timezone  = America/Chicago

[coop]
relay_a  = 22
relay_b  = 6
open_ms  = 30000
close_ms = 30000
open_at  = sunrise+30
close_at = sunset-60
```

`open_at` and `close_at` accept `sunrise` or `sunset` with an optional `+N` or `-N` minute offset.
Remove or comment them out to leave a door unscheduled.

> **Note:** The daemon will refuse to start if `token` is still set to `REPLACE_WITH_GENERATED_TOKEN`
> or is empty.  You must set a real generated token before the services will run.

See [`pi/doors.conf.example`](pi/doors.conf.example) for a fully annotated template.

### 4. Start the daemons

```bash
# systemd
sudo systemctl start chickengate chickenctl
sudo journalctl -u chickengate -f
sudo journalctl -u chickenctl  -f

# SysV init
sudo service chickengate start
sudo service chickenctl  start
sudo tail -f /var/log/chickengate.log /var/log/chickenctl.log
```

`chickengate` must be running before `chickenctl` — the systemd unit enforces this automatically.

### 5. Test from the command line

```bash
# Manual open/close (connects to chickengate socket directly)
sudo chickendoor open  coop
sudo chickendoor close coop

# Check daemon status via the HTTPS API
TOKEN=$(sudo grep '^token' /etc/chickendoor/doors.conf | cut -d= -f2 | tr -d ' ')
curl -k -H "Authorization: Bearer $TOKEN" https://localhost:8443/door/coop/status
```

---

## Home Assistant Installation

### HACS (recommended)

[![Open ChickenControl in HACS][hacs-badge]][hacs-open]

1. Click the button above, or go to **HACS → Integrations → ⋮ → Custom repositories**
   and add `https://github.com/hamster/ChickenControl` with category **Integration**.
1. Find **ChickenControl** in HACS and press **Download**.
1. Restart Home Assistant.

### Manual

1. Download the [latest release][releases] zip and unpack it.
1. Copy `custom_components/chickencontrol` into your HA config directory at
   `/config/custom_components/chickencontrol`.
1. Restart Home Assistant.

---

## Setup

After installation, add the integration:

[![Add ChickenControl to Home Assistant][ha-badge]][config-flow-start]

Or go to **Settings → Devices & Services → Add Integration** and search for **ChickenControl**.

### Step 1 — Connection

| Setting | Description |
|---------|-------------|
| **Host** | Hostname or IP address of the Raspberry Pi |
| **Port** | Port the daemon listens on (default `8443`) |
| **API Token** | The `token` value from `/etc/chickendoor/doors.conf` |
| **Verify TLS certificate** | Uncheck if using the self-signed certificate without adding it to HA's trust store |

The integration will connect to `GET /doors` to verify credentials and auto-discover the configured doors.

### Step 2 — Physical sensor mapping (optional)

After the integration is set up, click **Configure** to open the options flow. For each door you can optionally select an existing Home Assistant entity (a contact sensor, reed switch, etc.) to use as the authoritative open/closed source.

---

## Entities

The integration creates the following entities for each configured door (`coop`, `run`, etc.):

### Cover

| Entity | States | Description |
|--------|--------|-------------|
| `cover.{name}` | `open` `opening` `closing` `closed` | Door control and status tile |

The cover entity is the primary interface. It provides a tile card with open, close, and stop buttons and shows the direction of travel while the door is moving.

**Stop button behaviour:**
- Pressed while **closing** → immediately interrupts the close and reverses to open (emergency override)
- Pressed while **opening** → no-op (opening is not interrupted by design)

Sending an **open** command while the door is closing also triggers the same reversal — useful from automations or the command line.

### Sensor

| Entity | States | Description |
|--------|--------|-------------|
| `sensor.{name}_door` | `open` `closed` `moving` `unknown` | Current door state |

State priority:

1. **`moving`** — the Pi reports that a relay is currently energised (overrides the physical sensor)
2. **Physical sensor** (if mapped) — `on`/`open` → `open`, `off`/`closed` → `closed`
3. **Last commanded state** — from the Pi's state file when no physical sensor is mapped

### Buttons

| Entity | Description |
|--------|-------------|
| `button.{name}_open` | Sends an open command to the Pi |
| `button.{name}_close` | Sends a close command to the Pi |

Useful for automations where a direct open or close trigger is cleaner than using the cover entity.

When the Pi is unreachable all entities show as **unavailable**.

---

## How It Works

### Service architecture

ChickenControl splits work across two processes to limit the blast radius if the network-facing side is ever compromised:

```
Home Assistant
    │  HTTPS :8443
    ▼
chickenctl  (user: chickennet — no GPIO access)
    │  Unix socket  /run/chickengate.sock
    ▼
chickengate (user: chickenhw  — gpio group only)
    │  /dev/gpiomem
    ▼
relay hat → linear actuators
```

`chickengate` owns all hardware access and the solar scheduler. It never touches the network. `chickenctl` owns TLS termination and Bearer token authentication. It never touches GPIO. Even if an attacker fully compromises `chickenctl`, the worst they can do is send `open` or `close` for a door that is already configured — they cannot access arbitrary GPIO pins or escalate further.

### Solar scheduler

At startup, `chickengate` reads your latitude, longitude, and timezone from `doors.conf` and uses the [astral][astral] library to compute today's sunrise and sunset times. It then sleeps until each scheduled event and fires the appropriate door command. Times are recalculated at midnight each day. No cron jobs or external utilities are required.

```
chickengate starts
  └─ compute today's solar events (astral, pure Python, no network)
       └─ sleep until sunrise + 30 min
            └─ open coop
       └─ sleep until sunset − 60 min
            └─ close coop
  └─ midnight: recalculate for tomorrow
```

If the `[location]` section is absent from `doors.conf`, the scheduler is disabled and doors must be controlled manually via Home Assistant or `chickendoor`.

### Manual override (via Home Assistant)

```
HA cover open/close/stop
  └─ POST /door/coop/open   (HTTPS + Bearer token)
       └─ chickenctl validates token, forwards to socket
            └─ chickengate: background thread sets GPIO,
               holds relay for travel duration, clears GPIO
```

`chickenctl` returns `202 Accepted` immediately. The HA coordinator polls `GET /door/coop/status` every 5 seconds. While moving, the status is `opening` or `closing`. When the operation completes, it changes to `open` or `closed`.

### Interrupting a close

If an open command is received while the door is closing, `chickengate`:

1. Signals the close thread to stop
2. Waits for the relay to de-energise
3. Pauses 50 ms (relay settling time)
4. Starts the open operation

The reverse — closing while opening — is rejected with `409 Busy` by design, since an opening door is assumed to be safe.

### Relay safety sequencing

When switching direction, the code always de-energises the active relay before energising the opposite one, with a 50 ms pause in between. Both relays are never energised simultaneously.

### Concurrency

All door operations hold a per-door lockfile at `/run/chickendoor-{name}.lock`. The solar scheduler and manual commands go through the same command path inside `chickengate`, so they are serialised automatically and the same override rules apply.

---

## Hardware Reference

### Relay Hat

[Keyestudio 4-Channel Relay Shield][relay-hat] stacked directly on the Pi GPIO header. Each relay channel switches 12 V to one wire of a linear actuator.

### Wiring: Polarity Reversal via Dual SPDT Relay

Each actuator has two wires. Each wire connects to the COM (moving contact) of one relay. The NC terminal of every relay goes to GND; the NO terminal goes to +12 V.

```
      Relay A                  Relay B

  NO ──── +12V             NO ──── +12V
  │                        │
  COM ──── Wire A          COM ──── Wire B
  │                        │
  NC ──── GND              NC ──── GND

  Relay A OFF, Relay B OFF  →  Wire A = GND,  Wire B = GND  →  0 V, stopped (standby)
  Relay A ON,  Relay B OFF  →  Wire A = +12V, Wire B = GND  →  moves direction 1 (OPEN)
  Relay A OFF, Relay B ON   →  Wire A = GND,  Wire B = +12V →  moves direction 2 (CLOSE)
  Relay A ON,  Relay B ON   →  Wire A = +12V, Wire B = +12V →  0 V, stopped (avoid)
```

If a door moves the wrong direction, swap `relay_a` and `relay_b` in `doors.conf`.

### GPIO Pin Assignments (Pi 3B)

| Door | wiringPi | BCM | Physical | Role |
|------|----------|-----|----------|------|
| Coop | 3 | 22 | 15 | relay_a (open) |
| Coop | 22 | 6 | 31 | relay_b (close) |
| Run gate | 25 | 26 | 37 | relay_a (open) |
| Run gate | 7 | 4 | 7 | relay_b (close) |

Verified with `gpio readall` on the Pi 3B. All four pins show `Mode=OUT` and `V=0` at rest.

---

## License

GPL 3.0 — see [LICENSE](LICENSE).

[ha]: https://www.home-assistant.io/
[hacs]: https://hacs.xyz/
[hacs-badge]: https://my.home-assistant.io/badges/hacs_repository.svg
[hacs-open]: https://my.home-assistant.io/redirect/hacs_repository/?owner=hamster&repository=ChickenControl&category=integration
[ha-badge]: https://my.home-assistant.io/badges/config_flow_start.svg
[config-flow-start]: https://my.home-assistant.io/redirect/config_flow_start/?domain=chickencontrol
[releases]: https://github.com/hamster/ChickenControl/releases
[relay-hat]: https://www.keyestudio.com/products/keyestudio-rpi-4channel-relay-5v-shield-for-raspberry-pi-ce-certification
[astral]: https://pypi.org/project/astral/

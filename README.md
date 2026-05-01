# ChickenControl for Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?logo=homeassistantcommunitystore&logoColor=white)][hacs-open]
[![Version](https://img.shields.io/github/v/release/hamster/ChickenControl)][releases]
[![License](https://img.shields.io/github/license/hamster/ChickenControl)](LICENSE)

Automated chicken coop door and gate control running on a Raspberry Pi, with a [Home Assistant][ha] integration for manual overrides and status monitoring.

The Pi operates independently — doors open and close on a solar schedule via [sunwait][sunwait] even when the network is down. Home Assistant adds manual open/close buttons and a live door status sensor when the Pi is reachable on the LAN.

---

## Requirements

### Hardware

- Raspberry Pi (3B or newer) with [Keyestudio 4-Channel Relay Shield][relay-hat]
- One or two linear actuators (12 V, with internal limit switches)
- 12 V power supply for the actuators

### Software — Raspberry Pi

- Raspberry Pi OS (Bullseye or newer)
- Python 3.9+, `flask`, `waitress`, `RPi.GPIO` (or `rpi-lgpio` on Pi 5)
- [sunwait][sunwait] compiled and installed to `/usr/local/bin/sunwait`

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

- Install Python dependencies (`flask`, `waitress`, `RPi.GPIO` / `rpi-lgpio`)
- Create `/etc/chickendoor/` and `/var/lib/chickendoor/`
- Install `chickendoor` and `chickenctl` to `/usr/local/bin/`
- Copy `doors.conf.example` to `/etc/chickendoor/doors.conf`
- Generate a self-signed TLS certificate in `/etc/chickendoor/`
- Enable and register the `chickenctl` startup service (systemd preferred, SysV init fallback)

### 3. Configure doors

Edit `/etc/chickendoor/doors.conf`:

```bash
sudo nano /etc/chickendoor/doors.conf
```

At minimum, set the correct **BCM pin numbers** for your relay wiring and generate a **unique API token**:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

See [`pi/doors.conf.example`](pi/doors.conf.example) for a fully annotated template.

### 4. Start the daemon

```bash
# systemd
sudo systemctl start chickenctl
sudo journalctl -u chickenctl -f

# SysV init
sudo service chickenctl start
sudo tail -f /var/log/chickenctl.log
```

### 5. Set up the crontab

Create `/etc/cron.d/chickendoor` with entries like the following, substituting your latitude and longitude:

```
# /etc/cron.d/chickendoor

# Close coop door 60 minutes before sunset
0 16 * * * root /usr/local/bin/sunwait wait set offset -60 41.256N 95.995W; /usr/local/bin/chickendoor close coop

# Open coop door at sunrise
0 4 * * * root /usr/local/bin/sunwait wait rise 41.256N 95.995W; /usr/local/bin/chickendoor open coop
```

#### How sunwait works

[sunwait][sunwait] is a small utility that calculates sunrise/sunset for a given latitude and longitude and **blocks** until that moment arrives. The cron entry fires at a fixed time that is always safely before the earliest possible solar event at your latitude (4 AM for sunrise, 4 PM for sunset). `sunwait` then holds until the exact event, and `chickendoor` runs the moment it exits.

The `offset` flag shifts the trigger relative to the event — `offset -60` on the sunset entry closes the door 60 minutes *before* sunset rather than at sunset itself. Positive offsets delay past the event.

```
cron fires at 4:00 AM
  └─ sunwait wait rise 41.256N 95.995W
       └─ (blocks until sunrise, e.g. 6:47 AM)
            └─ chickendoor open coop
```

Build and install sunwait from [its GitHub page][sunwait]:

```bash
git clone https://github.com/risacher/sunwait.git
cd sunwait && make
sudo cp sunwait /usr/local/bin/
```

### 6. Test from the command line

```bash
# Manual open/close
sudo chickendoor open coop
sudo chickendoor close coop

# Check daemon status
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

### Scheduled operation (Pi-only, no network required)

```
cron fires at 4:00 AM
  └─ sunwait wait rise 41.256N 95.995W   ← blocks until sunrise
       └─ chickendoor open coop
```

See the [crontab setup section](#5-set-up-the-crontab) for full details and build instructions for sunwait. No network or Home Assistant involvement required.

### Manual override (via Home Assistant)

```
HA cover open/close/stop
  └─ POST /door/coop/open   (HTTPS + Bearer token)
       └─ chickenctl daemon
            └─ background thread: sets GPIO, holds relay for travel duration, clears GPIO
```

The daemon returns `202 Accepted` immediately. The coordinator polls `GET /door/coop/status` every 5 seconds. While moving, the status is `opening` or `closing` (direction is tracked). When the operation completes, the status changes to `open` or `closed`.

### Interrupting a close

If an open command is received while the door is closing, the daemon:

1. Signals the close thread to stop
2. Waits for the relay to de-energise
3. Pauses 50 ms (relay settling time)
4. Starts the open operation

The reverse — closing while opening — is rejected with `409 Busy` by design, since an opening door is assumed to be safe.

### Relay safety sequencing

When switching direction, the code always de-energises the active relay before energising the opposite one, with a 50 ms pause in between. Both relays are never energised simultaneously.

### Concurrency

Both `chickendoor` (cron) and `chickenctl` (daemon) use the same per-door lockfile at `/run/chickendoor-{name}.lock`. If cron fires while a manual command is in progress, `chickendoor` routes through the daemon, which applies the same override rules.

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
[sunwait]: https://github.com/risacher/sunwait

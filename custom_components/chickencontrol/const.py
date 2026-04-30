DOMAIN = "chickencontrol"

DEFAULT_PORT = 8443
DEFAULT_VERIFY_SSL = True
SCAN_INTERVAL_SECONDS = 5

CONF_TOKEN = "token"
CONF_VERIFY_SSL = "verify_ssl"
CONF_DOORS = "doors"

# Options (set after initial config)
OPT_SENSOR_PREFIX = "sensor_"  # + door name → entity_id of physical open/closed sensor

DOOR_STATES = ["open", "closed", "moving", "unknown"]

ICONS = {
    "open": "mdi:door-open",
    "closed": "mdi:door-closed",
    "moving": "mdi:swap-vertical",
    "unknown": "mdi:help-circle-outline",
}

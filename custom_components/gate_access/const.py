"""Constants for the Gate Access integration."""

DOMAIN = "gate_access"

# Config entry keys
CONF_TARGETS = "targets"          # list of entity_ids (gate, wicket, ...)
CONF_GATE_ENTITY = "gate_entity"  # legacy single-gate key (migrated on read)
CONF_LOG_PATH = "log_path"
CONF_CLOSE_AFTER = "close_after"        # legacy single value (migrated)
CONF_CLOSE_MAP = "close_after_map"      # per-target auto-close seconds
CONF_SHOW_CLOSE = "show_close"          # show a close button on the open page
CONF_ADMIN_ONLY = "admin_only"    # panel visible only to admins (else all users)
CONF_STATS = "stats"              # count opens per entity (day/month/year)
CONF_LOG_CLOSINGS = "log_closings"  # also record closings
CONF_DELETE_PASSWORD = "delete_password"  # optional gate for clearing history
CONF_RATE_LIMIT = "rate_limit"    # max opens per link per minute (0 = off)

DEFAULT_LOG_PATH = "/config/otwarcie_bramy.txt"
DEFAULT_CLOSE_AFTER = 0
DEFAULT_SHOW_CLOSE = False
DEFAULT_ADMIN_ONLY = True
DEFAULT_STATS = False
DEFAULT_LOG_CLOSINGS = False
DEFAULT_DELETE_PASSWORD = ""
DEFAULT_RATE_LIMIT = 2

TARGET_DOMAINS = [
    "cover",
    "lock",
    "switch",
    "input_boolean",
    "button",
    "input_button",
    "script",
    "light",
]

# Storage
STORAGE_VERSION = 1
STORAGE_KEY = "gate_access.users"

# Frontend
PANEL_URL_PATH = "gate-access"
PANEL_TITLE = "Brama – dostęp"
PANEL_ICON = "mdi:gate"
STATIC_URL = "/gate_access_static"

# Events / signals
EVENT_OPENED = f"{DOMAIN}_opened"

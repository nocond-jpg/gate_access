"""Constants for the Gate Access integration."""

DOMAIN = "gate_access"

# Config entry keys
CONF_TARGETS = "targets"          # list of entity_ids (gate, wicket, ...)
CONF_GATE_ENTITY = "gate_entity"  # legacy single-gate key (migrated on read)
CONF_LOG_PATH = "log_path"
CONF_CLOSE_AFTER = "close_after"        # legacy single value (migrated)
CONF_CLOSE_MAP = "close_after_map"      # per-target auto-close seconds
CONF_SHOW_CLOSE = "show_close"          # show a close button on the open page
CONF_BASE_URL = "base_url"              # fixed base URL for webhook links
CONF_CONFIRM_OPEN = "confirm_open"      # require a tap to open (iOS refresh-safe)
CONF_ADMIN_ONLY = "admin_only"    # panel visible only to admins (else all users)
CONF_STATS = "stats"              # count opens per entity (day/month/year)
CONF_LOG_CLOSINGS = "log_closings"  # also record closings
CONF_DELETE_PASSWORD = "delete_password"  # optional gate for clearing history
CONF_RATE_LIMIT = "rate_limit"    # max opens per link per minute (0 = off)
CONF_STATE_MAP = "state_map"      # per-target state entity (contact sensor etc.)
CONF_REMIND_MAP = "remind_map"    # per-target reminder minutes (0 = off)
CONF_APPROVE_NOTIFY = "approve_notify"  # notify service for new-device approvals

DEFAULT_LOG_PATH = "/config/otwarcie_bramy.txt"
DEFAULT_CLOSE_AFTER = 0
DEFAULT_SHOW_CLOSE = False
DEFAULT_BASE_URL = ""
DEFAULT_CONFIRM_OPEN = False
DEFAULT_ADMIN_ONLY = True
DEFAULT_STATS = False
DEFAULT_LOG_CLOSINGS = False
DEFAULT_DELETE_PASSWORD = ""
DEFAULT_RATE_LIMIT = 2
DEFAULT_REMIND_MIN = 10

# Mobile web app / Web Push
REMIND_REPEATS = 3                # reminders per opening (every N minutes)
PUSH_MAX_PER_USER = 5             # devices per person
PUSH_HOSTS = (                    # allowed push service hosts (blocks SSRF)
    "push.apple.com",
    "fcm.googleapis.com",
    "push.services.mozilla.com",
    "notify.windows.com",
)
# Device binding (1.8)
DEVICES_MAX_PER_USER = 10
DEVICES_PENDING_MAX = 3
PENDING_TTL_DAYS = 7
NONCE_TTL = 30
CAR_WEBHOOK_ID = "tesla"           # short pairing address for car browsers: /api/webhook/tesla
CAR_CODE_TTL = 300                 # pairing code lifetime (s)
CAR_CODE_MAX_FAIL = 10             # failed code attempts per CAR_CODE_FAIL_WINDOW before lock
CAR_CODE_FAIL_WINDOW = 600
APPROVE_ACTION_PREFIX = "GATE_ACCESS_"

STATE_DOMAINS = ["binary_sensor", "cover", "lock", "switch", "input_boolean"]

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

"""Constants for the Rivian Solar Charging integration."""

DOMAIN = "rivian_solar_charging"

# Config entry keys
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_VEHICLE_ID = "vehicle_id"
CONF_GRID_POWER_ENTITY = "grid_power_entity"
CONF_POWERWALL_ENTITY = "powerwall_entity"
CONF_POWERWALL_POWER_ENTITY = "powerwall_power_entity"
CONF_HOME_LAT = "home_latitude"
CONF_HOME_LNG = "home_longitude"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_CHARGE_LIMIT = "charge_limit"
CONF_BATTERY_LIMIT = "powerwall_min_pct"
CONF_POWERWALL_STOP_PCT = "powerwall_stop_pct"
CONF_RIVIAN_START_LIMIT = "rivian_start_limit"

# Stored session tokens
CONF_CSRF_TOKEN = "csrf_token"
CONF_APP_SESSION = "app_session"
CONF_USER_SESSION = "user_session"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_OTP_TOKEN = "otp_token"

# Defaults
DEFAULT_SCAN_INTERVAL = 300
DEFAULT_CHARGE_LIMIT = 90
DEFAULT_POWERWALL_MIN_PCT = 100
DEFAULT_POWERWALL_STOP_PCT = 70
DEFAULT_RIVIAN_START_LIMIT = 80

# Rivian on-board AC charger limits
MIN_AMPS = 8
MAX_AMPS = 48
VOLTAGE = 240

# Dead-band: don't change amps unless delta exceeds this
DEADBAND_AMPS = 2

# How close the car must be to home (km) to allow schedule updates
HOME_RADIUS_KM = 0.5

# Minutes before sunset to stop solar charging
SUNSET_CUTOFF_MINUTES = 30

# When charging should be "off", Rivian still requires an enabled schedule
# (see rivian_client.set_charging_schedule) — its 1-hour window is placed
# this many minutes ahead of "now" so it doesn't trigger immediately. That
# window is re-pushed further into the future once it gets within
# OFF_SCHEDULE_REFRESH_MARGIN_MINUTES of starting, so a static window never
# actually arrives and starts an unwanted charge session. The margin must
# be comfortably larger than the scan interval so a poll always catches the
# window before it becomes active.
OFF_SCHEDULE_LEAD_MINUTES = 12 * 60
OFF_SCHEDULE_REFRESH_MARGIN_MINUTES = 60

# Key used to signal "charge now" mode from the switch platform
CHARGE_NOW_KEY = "charge_now"

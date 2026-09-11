"""Constants for CitrineOS HA integration."""

DOMAIN = "citrine_ha"

PLATFORMS = ["sensor", "number", "button", "select"]

CONF_NAME = "name"
CONF_BASE_URL = "base_url"
CONF_TENANT_ID = "tenant_id"
CONF_AUTH_TOKEN = "auth_token"
CONF_VERIFY_SSL = "verify_ssl"
CONF_REQUEST_TIMEOUT = "request_timeout"

CONF_HASURA_URL = "hasura_url"
CONF_HASURA_TOKEN = "hasura_token"
CONF_HASURA_QUERY = "hasura_query"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_DEFAULT_ID_TAG = "default_id_tag"
CONF_DEFAULT_EVSE_ID = "default_evse_id"
CONF_EMS_ENDPOINT_PREFIX = "ems_endpoint_prefix"
CONF_EMS_TELEMETRY_SITE_ID = "ems_telemetry_site_id"
CONF_EMS_TELEMETRY_LIMIT = "ems_telemetry_limit"
CONF_EMS_TELEMETRY_STALE_SECS = "ems_telemetry_stale_secs"

DEFAULT_NAME = "CitrineOS"
DEFAULT_TENANT_ID = 1
DEFAULT_VERIFY_SSL = True
DEFAULT_REQUEST_TIMEOUT = 15
DEFAULT_SCAN_INTERVAL = 30
DEFAULT_DEFAULT_ID_TAG = "HA_REMOTE"
DEFAULT_DEFAULT_EVSE_ID = 1
DEFAULT_EMS_ENDPOINT_PREFIX = "ems"
DEFAULT_EMS_TELEMETRY_LIMIT = 500
DEFAULT_EMS_TELEMETRY_STALE_SECS = 180
DEFAULT_PROFILE_LIMIT = 7000.0
DEFAULT_PROFILE_SETPOINT = 0.0
DEFAULT_PROFILE_DISCHARGE_LIMIT = 0.0
DEFAULT_PROFILE_UNIT = "W"
DEFAULT_PROFILE_DURATION = 300
DEFAULT_PROFILE_STACK_LEVEL = 1
DEFAULT_PROFILE_PURPOSE = "TxProfile"
DEFAULT_PROFILE_OPERATION_MODE = "ChargingOnly"

DEFAULT_HASURA_QUERY = (
    "query ChargingStations($tenantId: Int!) {"
    " ChargingStations(where: {tenantId: {_eq: $tenantId}}) {"
    " id protocol isOnline chargePointVendor chargePointModel chargePointSerialNumber"
    " firmwareVersion tenantId locationId updatedAt latestOcppMessageTimestamp capabilities"
    " }"
    " Connectors(where: {tenantId: {_eq: $tenantId}}) {"
    " id stationId chargingStationId connectorId evseId status isOnline errorCode updatedAt"
    " }"
    " Transactions(where: {tenantId: {_eq: $tenantId}}, order_by: {updatedAt: desc}, limit: 500) {"
    " id stationId chargingStationId transactionId isActive startedAt stoppedAt updatedAt"
    " }"
    "}"
)

SERVICE_START_CHARGING = "start_charging"
SERVICE_STOP_CHARGING = "stop_charging"
SERVICE_SET_STATION_LIMIT = "set_station_limit"
SERVICE_SET_GROUP_LIMIT = "set_group_limit"
SERVICE_SET_CHARGING_PROFILE = "set_charging_profile"
SERVICE_CLEAR_CHARGING_PROFILE = "clear_charging_profile"
SERVICE_SYNC_DISCOVERY_NOW = "sync_discovery_now"
SERVICE_SYNC_EMS_TELEMETRY_NOW = "sync_ems_telemetry_now"
SERVICE_CLEAR_EMS_TELEMETRY_ERROR = "clear_ems_telemetry_error"

ATTR_ENTRY_ID = "entry_id"
ATTR_STATION_ID = "station_id"
ATTR_PROTOCOL = "protocol"
ATTR_ID_TAG = "id_tag"
ATTR_EVSE_ID = "evse_id"
ATTR_TRANSACTION_ID = "transaction_id"
ATTR_LIMIT = "limit"
ATTR_UNIT = "unit"
ATTR_DURATION = "duration"
ATTR_GROUP_ID = "group_id"
ATTR_STATION_IDS = "station_ids"
ATTR_PROFILE_ID = "profile_id"
ATTR_PROFILE_PURPOSE = "profile_purpose"
ATTR_PROFILE_KIND = "profile_kind"
ATTR_STACK_LEVEL = "stack_level"
ATTR_SETPOINT = "setpoint"
ATTR_DISCHARGE_LIMIT = "discharge_limit"
ATTR_OPERATION_MODE = "operation_mode"
ATTR_PROFILE_PERIODS = "profile_periods"
ATTR_SITE_ID = "site_id"
ATTR_TELEMETRY_LIMIT = "telemetry_limit"

DEFAULT_PROFILE_KIND = "Absolute"

# Load Controller Configuration Keys
CONF_GRID_POWER_SENSOR = "grid_power_sensor"
CONF_SOLAR_POWER_SENSOR = "solar_power_sensor"
CONF_BATTERY_POWER_SENSOR = "battery_power_sensor"
CONF_BATTERY_SOC_SENSOR = "battery_soc_sensor"
CONF_BATTERY_MIN_SOC = "battery_min_soc"
CONF_MAIN_FUSE_LIMIT_W = "main_fuse_limit_w"
CONF_SITE_EXPORT_LIMIT_W = "site_export_limit_w"
CONF_SOLAR_START_BUFFER_W = "solar_start_buffer_w"
CONF_MIN_CHARGE_CURRENT_A = "min_charge_current_a"
CONF_RAMP_RATE_W_S = "ramp_rate_w_s"
CONF_CONTROLLER_INTERVAL_SECS = "controller_interval_secs"
CONF_CONTROLLER_MODE = "controller_mode"
CONF_SITE_PHASES = "site_phases"
CONF_NOMINAL_VOLTAGE = "nominal_voltage"
CONF_DEADBAND_W = "deadband_w"
CONF_MIN_DWELL_SECS = "min_dwell_secs"
CONF_SITE_ID = "site_id"
CONF_MQTT_TOPIC_PREFIX = "mqtt_topic_prefix"

# Controller Operation Modes
MODE_OFF = "Off"
MODE_SOLAR_ONLY = "Solar Only"
MODE_SOLAR_BATTERY = "Solar + Battery"
MODE_GRID_CAPPED = "Grid Capped (Fast)"
MODE_DYNAMIC_DOE = "Dynamic Envelope (DOE)"
MODE_EMERGENCY_SAFE = "Emergency Safe"

CONTROLLER_MODES = [
    MODE_OFF,
    MODE_SOLAR_ONLY,
    MODE_SOLAR_BATTERY,
    MODE_GRID_CAPPED,
    MODE_DYNAMIC_DOE,
    MODE_EMERGENCY_SAFE,
]

# Controller Defaults
DEFAULT_MAIN_FUSE_LIMIT_W = 14400.0  # ~63A @ 230V single phase
DEFAULT_SITE_EXPORT_LIMIT_W = 5000.0
DEFAULT_SOLAR_START_BUFFER_W = 250.0
DEFAULT_MIN_CHARGE_CURRENT_A = 6.0
DEFAULT_RAMP_RATE_W_S = 500.0
DEFAULT_CONTROLLER_INTERVAL_SECS = 3
DEFAULT_CONTROLLER_MODE = MODE_SOLAR_ONLY
DEFAULT_SITE_PHASES = 1
DEFAULT_NOMINAL_VOLTAGE = 230.0
DEFAULT_BATTERY_MIN_SOC = 20.0
DEFAULT_DEADBAND_W = 250.0
DEFAULT_MIN_DWELL_SECS = 30
DEFAULT_SITE_ID = "home-site-1"
DEFAULT_MQTT_TOPIC_PREFIX = "citrine/ems"

# Services
SERVICE_SET_CONTROLLER_MODE = "set_controller_mode"
SERVICE_RECOMPUTE_LOAD_CONTROL = "recompute_load_control"
SERVICE_EMERGENCY_SAFE_MODE = "emergency_safe_mode"


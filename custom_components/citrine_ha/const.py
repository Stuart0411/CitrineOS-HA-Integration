"""Constants for CitrineOS HA integration."""

DOMAIN = "citrine_ha"

PLATFORMS = ["sensor", "number", "button"]

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

DEFAULT_NAME = "CitrineOS"
DEFAULT_TENANT_ID = 1
DEFAULT_VERIFY_SSL = True
DEFAULT_REQUEST_TIMEOUT = 15
DEFAULT_SCAN_INTERVAL = 30
DEFAULT_DEFAULT_ID_TAG = "HA_REMOTE"
DEFAULT_DEFAULT_EVSE_ID = 1

DEFAULT_HASURA_QUERY = (
    "query ChargingStations($tenantId: Int!) {"
    " ChargingStations(where: {tenantId: {_eq: $tenantId}}) {"
    " id protocol isOnline chargePointVendor chargePointModel chargePointSerialNumber"
    " firmwareVersion tenantId locationId updatedAt latestOcppMessageTimestamp"
    " }"
    " Connectors(where: {tenantId: {_eq: $tenantId}}) {"
    " id stationId chargingStationId connectorId evseId status isOnline updatedAt"
    " }"
    " Transactions(where: {tenantId: {_eq: $tenantId}}, order_by: {updatedAt: desc}, limit: 500) {"
    " id stationId chargingStationId transactionId isActive active startedAt stoppedAt updatedAt"
    " }"
    "}"
)

SERVICE_START_CHARGING = "start_charging"
SERVICE_STOP_CHARGING = "stop_charging"
SERVICE_SET_STATION_LIMIT = "set_station_limit"
SERVICE_SET_GROUP_LIMIT = "set_group_limit"
SERVICE_SYNC_DISCOVERY_NOW = "sync_discovery_now"

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

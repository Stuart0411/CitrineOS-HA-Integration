# CitrineOS Home Assistant Custom Component (Scaffold)

<p align="center">
  <img src="https://avatars.githubusercontent.com/u/132117031?s=200&v=4" alt="CitrineOS Logo" width="120" height="120" />
</p>

This repository now contains a scaffolded Home Assistant custom component for CitrineOS:

- Domain: `citrine_ha`
- Path: `custom_components/citrine_ha`
- Supports UI config flow
- Supports optional Hasura GraphQL discovery
- Creates charger devices and entities from discovery
- Supports start/stop charging and station/group load limit services

## Implemented capabilities

1. UI configuration (Config Entry)
- CitrineOS base URL
- Tenant ID
- Bearer token
- SSL verify toggle
- Request timeout
- Optional Hasura URL/token/query
- Discovery scan interval
- Default idTag and EVSE id for quick start commands

2. Discovery and entities
- Polls Hasura with GraphQL and maps discovered stations into HA devices
- Sensor per station for online state + metadata
- Additional diagnostics sensors for protocol, connector count, session state, and OCPP heartbeat age
- Number per station for max limit (W)
- Start/Stop button entities per station
- Dedicated charging profile UI entities (numbers, selects, and action buttons) so users can apply/clear profiles from dashboards without manual service calls

3. Control services
- `citrine_ha.start_charging`
- `citrine_ha.stop_charging`
- `citrine_ha.set_station_limit`
- `citrine_ha.set_group_limit`
- `citrine_ha.set_charging_profile`
- `citrine_ha.clear_charging_profile`
- `citrine_ha.sync_discovery_now`

4. Protocol-aware API calls
- OCPP 2.0.1 and OCPP 1.6 start/stop mappings
- OCPP 2.0.1 and OCPP 1.6 smart charging profile mappings for limits
- OCPP protocol normalization for mixed station metadata formats (for example `ocpp16`, `1.6`, `OCPP 2.0`)
- Endpoint fallback workarounds for deployments exposing `/ocpp/2.0/*` instead of `/ocpp/2.0.1/*`
- Retry workarounds for common profile issues (connector `0` rejection on OCPP 1.6, unit compatibility fallback)
- OCPP 2.0.1 `remoteStartId` can be sourced from transactions and incremented per station
- Per-station protocol and capability cache guides entity options and command payload selection

## Hasura query for stations, connectors, and transactions

Use this as the discovery query value in the config flow/options:

```graphql
query ChargingStations($tenantId: Int!) {
  ChargingStations(where: {tenantId: {_eq: $tenantId}}) {
    id
    protocol
    isOnline
    chargePointVendor
    chargePointModel
    chargePointSerialNumber
    firmwareVersion
    tenantId
    locationId
    latestOcppMessageTimestamp
    updatedAt
  }

  Connectors(where: {tenantId: {_eq: $tenantId}}) {
    id
    stationId
    chargingStationId
    connectorId
    evseId
    status
    isOnline
    updatedAt
  }

  Transactions(where: {tenantId: {_eq: $tenantId}}, order_by: {updatedAt: desc}, limit: 500) {
    id
    stationId
    chargingStationId
    transactionId
    isActive
    active
    startedAt
    stoppedAt
    updatedAt
  }
}
```

Notes:
- The integration now merges station + connector + transaction rows.
- Stop button and stop service can use discovered `active/current/previous` transaction id automatically.
- Start command can auto-select EVSE from connector rows.
- For OCPP 2.0.1, `remoteStartId` is derived from station transactions as `max(transactionId) + 1` when numeric, then incremented after each start.
- If your table/column names differ, adjust the query in options; the merge logic accepts station references from `stationId`, `chargingStationId`, or `identifier`.

## Load into Home Assistant

1. Copy this folder into your HA config path:
- `<config>/custom_components/citrine_ha`

2. Restart Home Assistant.

3. Add integration:
- Settings -> Devices & Services -> Add Integration -> "CitrineOS HA"

4. Fill config fields.

5. Verify devices are created for discovered chargers.

## Install via HACS

This repository is now HACS-ready with `hacs.json` at the repository root.

1. In Home Assistant, open HACS.
2. Go to Integrations.
3. Open the menu and select Custom repositories.
4. Add your repository URL and choose category Integration.
5. Search for CitrineOS HA in HACS and install it.
6. Restart Home Assistant.
7. Add the integration from Settings -> Devices & Services.

## Next hardening tasks (recommended)

1. Add explicit capability mapping per station (supported units, profile purpose, connector model).
2. Persist outbox/idempotency keys for retries and restart-safe command execution.
3. Replace equal-split group allocator with weighted/floor-based policy.
4. Add reconciliation loop using `getChargingProfiles`/`getCompositeSchedule`.
5. Add test suite (unit + integration mocks).

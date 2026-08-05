# CitrineOS Development Reference

## Workspace Layout

```
citrineos-core/          # Main monorepo (npm workspaces: base, core, Server)
  base/src/              # Shared interfaces, OCPP schemas, repositories, validators
    interfaces/
      api/               # REST API interfaces
      messages/          # OCPP message handlers
      modules/           # Module interfaces (ISmartChargingModule, etc.)
      repository.ts      # Base repository interface
      modules/OCPPValidator.ts  # AJV schema validation + runtime compatibility patches
  core/src/
    dal/                 # Sequelize models and repository implementations
    modules/             # Module implementations:
      Certificates/
      Configuration/
      EVDriver/
      Monitoring/
      OcppRouter/
      Reporting/
      SmartCharging/
      Tenant/
      Transactions/
  Server/                # Express entry point, docker-compose, migrations
    src/
    docker-compose.yml
  migrations/            # Sequelize CLI migrations (timestamp-named .ts files)

citrineos-operator-ui/   # Next.js operator dashboard
ocpp-evidence/           # Raw OCPP message captures for debugging
citrine-end-user-ui.html # Standalone end-user test UI (OCPP 2.1 charging profile builder)
```

## OCPP Protocol Knowledge

- This server implements OCPP 2.0.1 and OCPP 2.1.
- OCPP 2.1 adds `Dynamic` ChargingProfileKind, new profile purpose values (`ChargingStationExternalConstraints`, `PriorityCharging`, `LocalGeneration`), per-phase limits, V2G discharge, setpoints, operation modes, and advanced schedule fields.
- Schema validation uses AJV. Runtime compatibility patches (for example `evseId 0 -> 1` remapping for ReportChargingProfiles) live in `OCPPValidator.ts`.
- OCPP message handlers follow request/response/error patterns in `base/src/interfaces/messages/`.

## Known Patterns and Gotchas

### SmartCharging

- `SetChargingProfile` with `TxProfile` validates EVSE via `DeviceModelRepository.findEvseByIdAndConnectorId(tenantId, evseId, null)`.
- Missing EVSE rows cause "Evse not found" and should remain non-fatal (INFO-level behavior).
- For ChargingProfile persistence, exclude `transactionId` and `chargingSchedule` from `findOrCreate` and `update` defaults so only model-backed columns are persisted.
- For ChargingSchedule persistence, retry with remapped schedule ID on `UniqueConstraintError`.
- Charging profile schema wiring should use `SalesTariffs.chargingScheduleDatabaseId` and expose `salesTariff` via `HasOne` on `chargingScheduleDatabaseId`.

### Transactions

- If `TransactionEvent` has `evse.id` but omits `evse.connectorId`, infer connector and tariff only when exactly one connector exists for that station+evse.
- `totalKwh` can arrive as a string from GraphQL. Coerce with `Number(totalKwh)` and guard `Number.isFinite(...)` before calling `.toFixed()`.
- `MeterValueUtils.normalizeToKwh` should guard against extreme unit multipliers and non-finite outputs.
- `NotifyEvent` duplicate `eventId` replays can violate `(stationId, tenantId, eventId)` uniqueness. Use `readOrCreateByQuery` and unique-constraint fallback reads for idempotency.

### Authorization

- Authorization schema has been flattened and now includes a unique index. Check recent migrations before changing auth persistence logic.

### Docker Runtime

- `Server/docker-compose.yml` mounts `dist/` as volumes. After code changes, run:

```sh
docker compose exec -T citrine npm run build
docker compose restart citrine
```

- Rebuilding the image alone can leave stale compiled code.

## Build and Test Commands

```sh
# From citrineos-core/
npm run build               # TypeScript compile + tsc-alias path rewriting
npm run test                # Vitest (all tests)
npm run coverage            # Vitest with coverage
npm run lint                # ESLint
npm run lint-fix            # ESLint auto-fix + Prettier
npm run migrate             # Run pending Sequelize migrations

# Targeted vitest
npx vitest run base/test/modules/OCPPValidator.test.ts

# Docker
npm run start-docker        # docker compose up (Server/)
docker compose logs -f citrine
docker compose exec citrine sh
```

Always run `npm run build` at the monorepo root before targeted tests so `@citrineos/base` resolves correctly in Vitest.

## Migration Conventions

- File naming: `migrations/YYYYMMDDHHMMSS-descriptive-name.ts`
- Use `queryInterface.addColumn`, `removeColumn`, `changeColumn`, `addIndex`, and similar APIs.
- Wrap destructive changes in reversible `up` and `down` sections.
- Run `npm run migrate` after adding migrations.

## Testing Conventions

- Framework: Vitest (`vitest.config.ts` at monorepo root)
- Test locations: `base/test/**/*.test.ts`, `core/test/**/*.test.ts`
- Mock external systems (DB, cache, OCPP router) using `vi.mock()` and `vi.fn()`.
- Add regression tests in the closest suite to the patched code.
- Use `describe` blocks for class or function scopes and `it` blocks for behavior cases.

## TypeScript Conventions

- Strict mode enabled
- Path aliases:
  - `@citrineos/base` -> `base/src`
  - `@citrineos/core` -> `core/src`
- ESM modules (`"type": "module"`)
- Prefer `interface` over `type` for object shapes
- Repository pattern: extend `CrudRepository<Model>` from `@citrineos/base`

## Operator UI

- `citrineos-operator-ui/` is a Next.js TypeScript app.
- Local run:

```sh
docker compose -f docker-compose-local.yml up
```

- `citrine-end-user-ui.html` supports full OCPP 2.1 SetChargingProfile fields including advanced per-period values.

## Recommended Task Flow

1. Feature or module work:
   - Read existing implementation in `core/src/modules/<Module>/`
   - Read corresponding interfaces in `base/src/interfaces/modules/`
   - Implement and test
2. Bug fix:
   - Check `ocpp-evidence/`
   - Reproduce with a test
   - Patch and verify
3. Migration work:
   - Create a timestamped migration in `migrations/`
   - Implement `up` and `down`
   - Run migration
4. Docker debugging:
   - Inspect server logs
   - Rebuild mounted dist and restart service

## Constraints

- Do not drop or truncate tables without explicit confirmation.
- Do not push or force-push branches.
- Do not modify already-run production migration files; create a new migration instead.
- If spec behavior is ambiguous, cite the relevant OCPP 2.0.1 or OCPP 2.1 section.
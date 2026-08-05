---
name: citrineos-dev
description: "CitrineOS OCPP 2.0.1/2.1 development playbook. Use for debugging SmartCharging, Transactions, migrations, Vitest, Docker runtime behavior, and TypeScript/Sequelize module changes."
user-invocable: true
---

# CitrineOS Dev Skill

Use this skill when working in CitrineOS codebases that include OCPP 2.0.1 or OCPP 2.1 server behavior.

## When to Use

- Implementing or debugging modules in `core/src/modules/`
- Fixing OCPP schema or runtime compatibility behavior in `base/src/interfaces/modules/OCPPValidator.ts`
- Writing regression tests for SmartCharging, Transactions, and idempotency edge cases
- Creating and validating Sequelize migrations
- Diagnosing Docker stale-build behavior in the server container

## Procedure

1. Identify module and interface boundaries (`core/src/modules/` and `base/src/interfaces/modules/`).
2. Reproduce behavior with evidence in `ocpp-evidence/` and add or update tests.
3. Apply focused code changes with strict TypeScript typing and repository patterns.
4. Run build first, then targeted tests.
5. If running in Docker, rebuild dist in-container and restart the service.

## Commands

```sh
# From citrineos-core/
npm run build
npx vitest run <test-file>

# Docker runtime refresh after code changes
docker compose exec -T citrine npm run build
docker compose restart citrine
```

## Full Guidance

See [CitrineOS Development Reference](./references/citrineos-development-reference.md).
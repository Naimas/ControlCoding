# ControlCoding Cookbook - Worked Examples by Domain

This document provides concrete, domain-specific examples of ControlCoding adoption. Each example shows a realistic directory structure, zone classification, domain invariants, and configuration snippets. The complete Payments example (Section 15 of the methodology) demonstrates the full workflow including realistic hook failures.

**Note on `feature.lock`**: Directory structures in this document show the full L4 layout including `feature.lock` files. At L2-L3 (where most adopters start), boundary enforcement uses `.claude/cc_config.json` + hooks instead. `feature.lock` as an executable artifact is planned for v2.2.

**Related documents:**
- [methodology.md](methodology.md) - Core concepts, principles, enforcement model
- [hooks-reference.md](hooks-reference.md) - Hook scripts and configuration
- [tools-reference.md](tools-reference.md) - CLAUDE.md, CLI, MCP servers

---

## 1 Django / Web Application

### 1.1 Directory structure and zone classification

```
myproject/
  stable/                    # DENY - core business logic
    core/
      models.py              # Domain models (source of truth)
      permissions.py         # Authorization rules
      validators.py          # Domain validation
  shared/                    # WARN - cross-feature utilities
    utils/
      email.py
      pagination.py
    middleware/
      audit.py
  features/                  # Open - active development
    orders/
      feature.lock
      models.py
      views.py
      serializers.py
      tests/
        test_invariants.py
    inventory/
      feature.lock
      models.py
      views.py
      tests/
        test_invariants.py
  workspace/                 # Open - experiments
    prototypes/
    spikes/
```

### 1.2 Domain invariants

```python
# features/orders/tests/test_invariants.py

def test_invariant_order_total_consistency():
    """Order total must equal sum of line items + tax - discounts."""
    order = create_test_order_with_items()
    expected = sum(item.price * item.qty for item in order.items)
    expected += order.tax_amount - order.discount_amount
    assert order.total == expected, f"drift: {abs(order.total - expected)}"

def test_invariant_stock_never_negative():
    """Inventory stock count must never go below zero."""
    product = Product.objects.get(pk=1)
    process_order(product, quantity=product.stock + 1)
    product.refresh_from_db()
    assert product.stock >= 0, f"stock went negative: {product.stock}"

def test_invariant_audit_trail_immutable():
    """Audit log entries must never be modified or deleted."""
    initial_count = AuditLog.objects.count()
    initial_hashes = [hash(entry.content) for entry in AuditLog.objects.all()]
    perform_business_operations()
    assert AuditLog.objects.count() >= initial_count
    for i, entry in enumerate(AuditLog.objects.all()[:initial_count]):
        assert hash(entry.content) == initial_hashes[i]
```

### 1.3 cc_config.json snippet

```json
{
  "protected_zones": [
    {
      "pattern": "stable/",
      "level": "DENY",
      "reason": "Core business models and permissions. Requires team review."
    },
    {
      "pattern": "shared/middleware/",
      "level": "WARN",
      "reason": "Middleware affects all requests. Verify side effects."
    }
  ]
}
```

### 1.4 CLAUDE.md excerpt

```markdown
## Architecture Rules
- Model vs View: Django models in stable/core/ are the source of truth.
  Serializers and views are "views" - they read from models, never bypass them.
- All database writes go through model methods, never raw SQL in views.
- Order total is computed, never stored independently from line items.

## Domain Invariants
- Order total = sum(line_items) + tax - discounts (0% drift tolerance)
- Stock count >= 0 (enforced at model level, tested in invariants)
- Audit log is append-only (no UPDATE or DELETE)

## Module Boundaries
### Stable (do NOT modify without explicit approval)
- stable/core/ - Domain models, permissions, validators
### Features (active development)
- features/orders/ - Order processing
- features/inventory/ - Stock management
```

---

## 2 React SPA

### 2.1 Directory structure and zone classification

```
src/
  core/                      # DENY - state management, source of truth
    store/
      rootReducer.ts
      middleware/
        validation.ts
    types/
      domain.ts              # Shared domain types
    constants.ts
  shared/                    # WARN - cross-feature components
    components/
      DataTable/
      Modal/
      FormField/
    hooks/
      useAuth.ts
      usePagination.ts
    api/
      client.ts
      interceptors.ts
  features/                  # Open - active development
    dashboard/
      feature.lock
      components/
      hooks/
      __tests__/
    user-profile/
      feature.lock
      components/
      hooks/
      __tests__/
  workspace/                 # Open - experiments
    experiments/
    design-spikes/
```

### 2.2 Domain invariants

```typescript
// src/features/dashboard/__tests__/invariants.test.ts

describe("Domain Invariants", () => {
  it("store state shape never loses required fields", () => {
    const state = store.getState();
    expect(state).toHaveProperty("auth.user");
    expect(state).toHaveProperty("auth.permissions");
    expect(state).toHaveProperty("dashboard.widgets");
    // Shape validation - no field silently disappears after refactoring
  });

  it("computed values derive from store, never from local state", () => {
    // Model vs View: the Redux store is the Model.
    // Components are Views - they read, never independently compute totals.
    const { totalRevenue } = selectDashboardMetrics(store.getState());
    const manualSum = store.getState().dashboard.widgets
      .reduce((sum, w) => sum + w.revenue, 0);
    expect(totalRevenue).toBe(manualSum);
  });

  it("API client always includes auth headers", () => {
    // External connection invariant
    const request = apiClient.interceptors.request.handlers[0];
    expect(request).toBeDefined();
    const config = request.fulfilled({ headers: {} });
    expect(config.headers).toHaveProperty("Authorization");
  });
});
```

### 2.3 cc_config.json snippet

```json
{
  "protected_zones": [
    {
      "pattern": "src/core/",
      "level": "DENY",
      "reason": "State shape and domain types. Changes affect every feature."
    },
    {
      "pattern": "src/shared/api/",
      "level": "WARN",
      "reason": "API client shared by all features. Verify interceptor chain."
    }
  ]
}
```

### 2.4 CLAUDE.md excerpt

```markdown
## Architecture Rules
- Model vs View: Redux store is the single source of truth.
  Components render from store state, never maintain independent copies.
- All API calls go through shared/api/client.ts (never raw fetch in features).
- Domain types in core/types/ are the contract between features.

## Domain Invariants
- Store state shape: required fields never removed (tested in invariants)
- Computed values: always derived from store, never from component state
- API auth: every request includes Authorization header

## Module Boundaries
### Stable
- src/core/ - Store, types, constants
### Shared
- src/shared/ - Components, hooks, API client
### Features
- src/features/dashboard/ - Dashboard widgets
- src/features/user-profile/ - User settings
```

---

## 3 C++ Game / Simulation

### 3.1 Directory structure and zone classification

```
src/
  stable/                    # DENY - engine core
    core/
      WorldState.h/cpp       # Single source of truth
      EventBus.h/cpp         # Synchronous, deterministic
      ParamRegistry.h/cpp    # Read-only during simulation
    physics/
      PhysicsEngine.h/cpp
      collision/
    math/
      Vector3.h
      Matrix4.h
      noise/
  shared/                    # WARN - cross-module utilities
    interfaces/
      IRenderable.h
      ISimulatable.h
      ISerializable.h
    utils/
      Logger.h/cpp
      Profiler.h/cpp
  features/                  # Open - active development
    terrain/
      feature.lock
      src/
        systems/
          TerrainGenerator/
          ErosionSystem/
      tests/
        test_invariants.cpp
    climate/
      feature.lock
      src/
        systems/
          AtmosphereSystem/
      tests/
        test_invariants.cpp
  workspace/                 # Open - experiments
    prototypes/
    benchmarks/
```

### 3.2 Domain invariants

```cpp
// features/terrain/tests/test_invariants.cpp

TEST(DomainInvariants, MassConservation) {
    // Total mass (solid + water + atmosphere) must be constant
    WorldState state = createTestWorld(seed: 42);
    double initial_mass = state.totalSolidMass()
                        + state.totalWaterMass()
                        + state.totalAtmosphereMass();

    simulateSteps(state, 1000);

    double final_mass = state.totalSolidMass()
                      + state.totalWaterMass()
                      + state.totalAtmosphereMass();

    EXPECT_NEAR(initial_mass, final_mass, 1e-10)
        << "Mass drift: " << std::abs(final_mass - initial_mass);
}

TEST(DomainInvariants, EnergyConservation) {
    // In a closed system, total energy must be conserved
    WorldState state = createTestWorld(seed: 42);
    double initial_energy = state.totalEnergy();

    simulateSteps(state, 1000);

    double final_energy = state.totalEnergy();
    EXPECT_NEAR(initial_energy, final_energy, 1e-8)
        << "Energy drift: " << std::abs(final_energy - initial_energy);
}

TEST(DomainInvariants, DeterministicReplay) {
    // Same seed must produce identical results
    WorldState state1 = createTestWorld(seed: 42);
    WorldState state2 = createTestWorld(seed: 42);

    simulateSteps(state1, 500);
    simulateSteps(state2, 500);

    EXPECT_EQ(state1.hash(), state2.hash())
        << "Determinism violated: different hashes for same seed";
}
```

### 3.3 cc_config.json snippet

```json
{
  "protected_zones": [
    {
      "pattern": "src/stable/",
      "level": "DENY",
      "reason": "Engine core: WorldState, EventBus, Physics. Requires full test suite pass."
    },
    {
      "pattern": "src/shared/interfaces/",
      "level": "WARN",
      "reason": "Interfaces are contracts. Adding methods affects all implementors."
    }
  ]
}
```

### 3.4 CLAUDE.md excerpt

```markdown
## Architecture Rules
- Model vs View: WorldState is the Model (authoritative data).
  Renderer reads from WorldState, never modifies it.
  All mutations go through simulation systems, never through UI/renderer.
- EventBus is synchronous and deterministic. No async, no threading in event handlers.
- ParamRegistry is read-only during simulation. Write only during setup/editor phase.

## Domain Invariants
- Mass conservation: total mass constant across simulation steps (tolerance: 1e-10)
- Energy conservation: total energy constant in closed system (tolerance: 1e-8)
- Deterministic replay: same seed produces identical hash after N steps
- Height values: always within [planet_min_radius, planet_max_radius]

## Module Boundaries
### Stable (DENY - extraordinary review required)
- src/stable/core/ - WorldState, EventBus, ParamRegistry
- src/stable/physics/ - Physics engine, collision
- src/stable/math/ - Vector, Matrix, noise functions
### Features (active development)
- features/terrain/ - Terrain generation, erosion
- features/climate/ - Atmosphere simulation
```

---

## 4 Python Data Pipeline

### 4.1 Directory structure and zone classification

```
pipeline/
  stable/                    # DENY - core pipeline engine
    engine/
      orchestrator.py        # DAG execution engine
      scheduler.py           # Task scheduling
    schemas/
      data_contracts.py      # Input/output schemas
    validators/
      quality_checks.py      # Data quality rules
  shared/                    # WARN - cross-pipeline utilities
    connectors/
      postgres.py
      s3.py
      api_client.py
    transforms/
      cleaning.py
      normalization.py
    monitoring/
      metrics.py
      alerts.py
  features/                  # Open - individual pipelines
    etl_customers/
      feature.lock
      extract.py
      transform.py
      load.py
      tests/
        test_invariants.py
    etl_transactions/
      feature.lock
      extract.py
      transform.py
      load.py
      tests/
        test_invariants.py
  workspace/                 # Open - experiments
    notebooks/
    ad_hoc_queries/
```

### 4.2 Domain invariants

```python
# features/etl_transactions/tests/test_invariants.py

def test_invariant_row_count_preservation():
    """No rows silently dropped during transform phase."""
    raw_data = extract_sample()
    transformed = transform(raw_data)
    # Rows can be split (1->N) or filtered (with explicit log),
    # but never silently lost
    assert transformed.row_count >= raw_data.row_count * 0.99, (
        f"Lost {raw_data.row_count - transformed.row_count} rows "
        f"({(1 - transformed.row_count/raw_data.row_count)*100:.1f}%)"
    )

def test_invariant_monetary_sum_preserved():
    """Total monetary value must be preserved through transformations."""
    raw_data = extract_sample()
    transformed = transform(raw_data)
    raw_total = raw_data["amount"].sum()
    transformed_total = transformed["amount"].sum()
    assert abs(raw_total - transformed_total) < 0.01, (
        f"Monetary drift: {abs(raw_total - transformed_total):.2f}"
    )

def test_invariant_schema_conformance():
    """Output must conform to the declared data contract."""
    from stable.schemas.data_contracts import TransactionOutputSchema
    transformed = transform(extract_sample())
    errors = TransactionOutputSchema.validate(transformed)
    assert not errors, f"Schema violations: {errors}"

def test_invariant_idempotent_load():
    """Running the same load twice must not duplicate records."""
    data = transform(extract_sample())
    load(data)
    load(data)  # Second run
    count = query_target_table_count()
    assert count == len(data), f"Expected {len(data)}, got {count} (duplicates)"
```

### 4.3 cc_config.json snippet

```json
{
  "protected_zones": [
    {
      "pattern": "stable/",
      "level": "DENY",
      "reason": "Pipeline engine and data contracts. Changes affect all pipelines."
    },
    {
      "pattern": "shared/connectors/",
      "level": "WARN",
      "reason": "Database connectors shared by all ETL jobs. Verify connection handling."
    }
  ]
}
```

### 4.4 CLAUDE.md excerpt

```markdown
## Architecture Rules
- Data contracts in stable/schemas/ are the source of truth for all pipeline I/O.
  Every pipeline validates output against its contract before loading.
- All database access goes through shared/connectors/ (never raw connections in features).
- Transforms must be pure functions: same input produces same output, no side effects.

## Domain Invariants
- Row count preservation: no silent data loss (>99% rows retained or explicitly logged)
- Monetary sum preservation: total amounts unchanged through transforms (tolerance: 0.01)
- Schema conformance: output matches data contract
- Idempotent loads: re-running a load does not duplicate records

## Module Boundaries
### Stable
- stable/engine/ - DAG orchestrator, scheduler
- stable/schemas/ - Data contracts (input/output schemas)
### Features
- features/etl_customers/ - Customer data pipeline
- features/etl_transactions/ - Transaction data pipeline
```

---

## 5 Financial System

### 5.1 Directory structure and zone classification

```
finapp/
  stable/                    # DENY - financial core
    ledger/
      double_entry.py        # Double-entry bookkeeping engine
      account_types.py       # Chart of accounts structure
      journal.py             # Journal entry processing
    compliance/
      aml_rules.py           # Anti-money laundering
      reporting.py           # Regulatory reporting
    audit/
      trail.py               # Immutable audit log
      reconciliation.py      # Account reconciliation
  shared/                    # WARN - cross-feature services
    currency/
      exchange.py
      rounding.py            # Banker's rounding rules
    auth/
      permissions.py
      role_hierarchy.py
    notifications/
      alerts.py
  features/                  # Open - active development
    payments/
      feature.lock
      src/
        systems/
          TransactionProcessor/
          RetryManager/
      tests/
        test_invariants.py
    invoicing/
      feature.lock
      src/
        invoice_generator.py
        tax_calculator.py
      tests/
        test_invariants.py
  workspace/                 # Open - experiments
    analysis/
    migration_scripts/
```

### 5.2 Domain invariants

```python
# features/payments/tests/test_invariants.py

def test_invariant_balance_consistency():
    """Total debits must equal total credits (double-entry)."""
    ledger = get_test_ledger()
    process_payment(ledger, amount=100.00, from_acct="A", to_acct="B")
    total_debits = sum(e.amount for e in ledger.entries if e.type == "debit")
    total_credits = sum(e.amount for e in ledger.entries if e.type == "credit")
    drift = abs(total_debits - total_credits)
    assert drift == 0, f"Balance drift: {drift}"

def test_invariant_idempotency():
    """Retrying a payment must not double-charge."""
    ledger = get_test_ledger()
    tx_key = "TX-2026-001"
    process_payment(ledger, amount=50.00, tx_key=tx_key)
    process_payment(ledger, amount=50.00, tx_key=tx_key)  # Retry
    charges = [e for e in ledger.entries if e.tx_key == tx_key and e.type == "debit"]
    assert len(charges) == 1, f"Double charge: {len(charges)} debits for {tx_key}"

def test_invariant_audit_immutability():
    """Audit trail entries must never be modified after creation."""
    trail = get_audit_trail()
    initial_hash = trail.compute_chain_hash()
    perform_business_operations()
    # Verify no historical entries were modified
    assert trail.verify_chain_integrity(), "Audit chain broken"
    # Verify entries only appended, never removed
    assert trail.compute_chain_hash()[:len(initial_hash)] == initial_hash

def test_invariant_rounding():
    """Currency rounding must use banker's rounding (round half to even)."""
    # 0.005 -> 0.00, 0.015 -> 0.02, 0.025 -> 0.02
    from shared.currency.rounding import round_currency
    assert round_currency(0.005) == 0.00
    assert round_currency(0.015) == 0.02
    assert round_currency(0.025) == 0.02
```

### 5.3 cc_config.json snippet

```json
{
  "protected_zones": [
    {
      "pattern": "stable/ledger/",
      "level": "DENY",
      "reason": "Double-entry engine. Modifications require compliance review."
    },
    {
      "pattern": "stable/compliance/",
      "level": "DENY",
      "reason": "AML and regulatory rules. Legal and compliance sign-off required."
    },
    {
      "pattern": "stable/audit/",
      "level": "DENY",
      "reason": "Audit trail integrity. Immutability is a regulatory requirement."
    },
    {
      "pattern": "shared/currency/",
      "level": "WARN",
      "reason": "Rounding rules affect all monetary calculations."
    }
  ]
}
```

### 5.4 CLAUDE.md excerpt

```markdown
## Architecture Rules
- Double-entry bookkeeping: every transaction creates exactly one debit and one credit.
  The ledger in stable/ledger/ is the source of truth for all account balances.
- Audit trail is append-only. No UPDATE, no DELETE, no TRUNCATE on audit tables.
- All monetary arithmetic uses banker's rounding via shared/currency/rounding.py.
- All payments must include a transaction key for idempotency.

## Domain Invariants
- Balance consistency: total debits == total credits (0% drift, always)
- Idempotency: same tx_key produces exactly one charge
- Audit immutability: chain hash integrity verified on every session end
- Currency rounding: banker's rounding (round half to even) for all amounts

## Module Boundaries
### Stable (DENY - requires compliance review)
- stable/ledger/ - Double-entry engine
- stable/compliance/ - AML, regulatory reporting
- stable/audit/ - Immutable audit trail
### Features (active development)
- features/payments/ - Transaction processing, retry logic
- features/invoicing/ - Invoice generation, tax calculation
```

---

## 6 Complete Example: Adding Retry Logic to Payments

This section reproduces the complete worked example from the methodology (Section 15), demonstrating the full ControlCoding workflow with realistic hook failures and corrections.

### 6.1 Request

"Add retry logic with exponential backoff for failed transactions."

### 6.2 Plan Mode

The AI enters plan mode and:

1. Reads CLAUDE.md, understands Model vs View, invariants
2. Reads features/Payments/feature.lock, understands the boundaries
3. Explores features/Payments/src/, finds existing TransactionProcessor
4. Consults shared/FUNCTION_REGISTRY.yml, finds available utilities
5. Proposes plan:

```
Plan:
- Modify: features/Payments/src/systems/TransactionProcessor/TransactionProcessor.py
- Add: features/Payments/src/systems/RetryManager/RetryManager.py
- Add: features/Payments/tests/invariants/test_retry_idempotency.py
- DO NOT touch: features/Payments/include/** (API unchanged)
- DO NOT touch: stable/** (foundations)
- Invariants to preserve: balance_consistency (0% drift), idempotency
```

### 6.3 Human approval

The human approves the plan (or requests changes).

### 6.4 Implementation (with realistic failures)

The AI implements the RetryManager. At every write attempt the `check_boundaries.py` hook verifies the file is within perimeter.

**First attempt, hook blocks:**

The AI decides the retry needs a new `TransactionRetried` event and attempts to add the type in `stable/core/events/EventTypes.py`. The hook responds:

```
CONTROL CODING: stable/ is FROZEN. Extraordinary review required.
```

The AI cannot proceed. It must find an alternative. Solution: use a generic event already present in stable (`CustomEvent` with string payload) or define the event inside `features/Payments/src/events/`. It chooses the second option, which stays within perimeter. The hook approves.

**Second attempt, invariant test fails:**

The AI implements the retry, runs the tests:

```
test_invariant_balance_consistency ... FAIL (drift: 2.30%)
```

The retry was re-executing the transaction without checking if the first one had succeeded, duplicating the charge. The invariant test caught the bug before it reached the commit. The AI adds an idempotency check based on the transaction key and re-runs:

```
test_invariant_balance_consistency ... PASS (drift: 0.00%)
test_invariant_idempotency .......... PASS (retries: identical result)
```

**Third attempt, success:**

```
test_invariant_balance_consistency ... PASS (drift: 0.00%)
test_invariant_idempotency .......... PASS (retries: identical result)
test_invariant_determinism .......... PASS (hash: identical)
test_payments_retry_backoff ......... PASS
test_payments_retry_max_attempts .... PASS
test_perf_transaction_time .......... PASS (p95: 180ms)
```

All gates green. The commit passes.

### 6.5 What would have happened without ControlCoding

Without the boundary hook, the AI would have modified `stable/core/events/EventTypes.py` adding the new event type. No immediate error, but every other module that imports EventTypes could have unforeseen side effects. The bug would surface weeks later, in a different module, with no trace of the cause.

Without the balance consistency invariant test, the double transaction bug would have reached production. The functional test ("the retry returns success") passed. The problem was in the conservative property of the system ("total money doesn't change"), which only a domain invariant verifies.

### 6.6 Commit

```
feat(payments): add exponential backoff retry for failed transactions

- Implements RetryManager with configurable max attempts and backoff
- Ensures idempotency via transaction key deduplication
- Balance consistency maintained: 0.00% drift
- Transaction time p95: 180ms (budget: 200ms)
```

---

## 7 Patterns Across Domains

### 7.1 Identifying your invariants

Every domain has conservation laws or consistency rules. Common patterns:

| Domain | Typical Invariants |
|---|---|
| Financial | Balance consistency, idempotency, audit immutability, rounding |
| Physics/Simulation | Mass conservation, energy conservation, deterministic replay |
| E-commerce | Order total = sum(items), stock >= 0, price > 0 |
| Data Pipeline | Row count preservation, schema conformance, idempotent loads |
| Healthcare | Patient ID uniqueness, medication dosage bounds, consent chain |
| Game Engine | Entity count consistency, physics bounds, save/load fidelity |

### 7.2 Choosing the right adoption level

Not every project needs Level 4 governance. Match the level to your project:

```
Level 1 (Documentary):  Any project, day 1
  -> CLAUDE.md + operative rules + basic tests
  -> Cost: 30 minutes

Level 2 (Structural):   First domain module with business logic
  -> Add domain invariant tests + AppState extraction
  -> Cost: 2-4 hours

Level 3 (Communication): 2+ modules exchanging data
  -> Event Bus + cross-module invariant tests
  -> Cost: 1-2 days

Level 4 (Governance):   Stable modules that must not break
  -> feature.lock + boundary hooks + CI pipeline
  -> Cost: 1-2 days
```

### 7.3 Common mistakes

1. **Implementing everything at once.** Start with Level 1. Add structure when triggers fire, not before.

2. **Protecting the wrong things.** DENY zones should protect code that is stable and critical, not code that is actively being developed. Protecting features/ with DENY defeats the purpose.

3. **Writing functional tests instead of invariant tests.** "The retry returns success" is a functional test. "Total money doesn't change" is an invariant test. Both are needed; only the second catches the double-charge bug.

4. **Skipping the plan.** Without a plan, the AI writes code first and discovers boundary violations after wasting tokens. Plan mode catches violations at zero cost.

5. **Making invariant tests too loose.** A tolerance of 5% on financial balances catches nothing. Set tolerances based on domain requirements, not convenience.

6. **Letting the AI rewrite specification documents.** AI agents compress rich requirements into telegraphic shorthand across successive rewrites (semantic drift by compression). After 2-3 compression cycles, the spec is semantically different from the original. Put specification documents in DENY zones and write requirements as atomic items with IDs, concrete examples, and anti-examples. See methodology Section 9.14.

---

## 8 Verification Agent: Worked Examples

The Vision Agent verifies that a built project matches its design document. Two modes cover different project types.

### 8.1 Build Mode (visual projects - games, 3D, rich UIs)

Build mode prevents the "build everything, test nothing" failure. The AI implements one criterion at a time and verifies immediately.

**Setup** (`python cc.py install vision`):

```bash
# 1. AI reads the design doc and extracts criteria
python tools/verification_agent.py add-criterion \
  --id C-01 --text "A sword with a visible blade and hilt" \
  --method visual --layer 1 --section "## Weapons"

python tools/verification_agent.py add-criterion \
  --id C-02 --text "Horizontal and vertical slashing motions" \
  --method visual --layer 2 --blocks C-03 \
  --steps "trigger horizontal slash; screenshot; trigger vertical; screenshot; compare"

# 2. Ask "what should I build next?"
python tools/verification_agent.py next --layer 1
# -> [NEXT] C-01: "A sword with a visible blade and hilt"
#    Method: visual | Layer: 1 | Attempts: 0/3

# 3. AI implements C-01, takes screenshot, verifies
python tools/visual_check.py --exe build/app.exe --output screenshots/sword.png

# 4. AI reads screenshot, compares to criterion, reports result
python tools/verification_agent.py update \
  --id C-01 --status pass --evidence screenshots/sword.png

# 5. Check for regressions on all previously-passed criteria
python tools/verification_agent.py check-regression

# 6. Repeat from step 2 until layer complete
python tools/verification_agent.py next --layer 1
# -> [NEXT] Layer 1 complete (12/12 pass). Ready to advance to Layer 2.
```

### 8.2 Audit Mode (backend projects - APIs, CLIs, data pipelines)

Audit mode verifies a project that has already been built.

```bash
# 1. Extract all criteria from the design document
python tools/verification_agent.py add-criterion \
  --id C-01 --text "Invoice subtotals must equal sum of line items" \
  --method numerical --layer 0

python tools/verification_agent.py add-criterion \
  --id C-02 --text "PDF output must include company logo in header" \
  --method visual --layer 0 --blocks C-03

python tools/verification_agent.py add-criterion \
  --id C-03 --text "Email sending must attach the generated PDF" \
  --method functional --layer 0

# 2. AI verifies each criterion and updates status
python tools/verification_agent.py update \
  --id C-01 --status pass --evidence test_output.log

python tools/verification_agent.py update \
  --id C-02 --status fail --notes "logo not found in header area of generated PDF"
# -> C-03 is auto-BLOCKED (depends on C-02)

# 3. Check convergence status
python tools/verification_agent.py convergence-status
# -> 1/3 pass (33.3%), 1 failed, 1 blocked

# 4. AI fixes C-02, re-verifies
python tools/verification_agent.py update \
  --id C-02 --status pass --evidence screenshots/pdf_header.png

# 5. Generate final report
python tools/verification_agent.py report \
  --output devlog/verification_report.json --design CLAUDE.md
```

### 8.3 Using MCP tools (AI-to-AI verification)

When the Vision Agent MCP server is configured, another AI session can drive verification programmatically:

```python
# Building AI calls MCP tools directly
result = vision_add_criterion(
    criterion_id="C-01",
    text="Invoice subtotals must equal sum of line items",
    method="numerical", layer=0)

result = vision_next()
# -> 'C-01: "Invoice subtotals must equal sum of line items"\nMethod: numerical ...'

result = vision_update(criterion_id="C-01", status="pass", evidence="test.log")

result = vision_report(output_dir="devlog/")
# -> 'Total: 1 | Passed: 1 (100.0%) | Failed: 0 | Blocked: 0\nReports: ...'
```

### 8.4 Anti-compression in practice

The verification agent enforces verbatim requirement preservation. This prevents the "cascading compression" failure where requirements lose detail across AI rewrites.

**What happens if text is tampered**:
```
$ python tools/verification_agent.py next
[VERIFY] CRITERIA DRIFT DETECTED: C-01 original_text modified
[VERIFY] Expected hash: a1b2c3...
[VERIFY] Current hash:  d4e5f6...
(exits with code 2)
```

**What happens without evidence**:
```
$ python tools/verification_agent.py update --id C-01 --status pass
[VERIFY] ERROR: --evidence required when status=pass
```

**What happens without diagnosis**:
```
$ python tools/verification_agent.py update --id C-01 --status fail
[VERIFY] ERROR: --notes required when status=fail
```

Feature: Per‑currency FX tolerance configuration, suppression, alerts and audit
  # ---------- Configure Currency Tolerances ----------
  @AC1
  Scenario: UI allows entry of tolerance values for supported and additional currencies
    Given the system is running
    When an authorized user opens the tolerance configuration UI
    And the user enters tolerance values for "USD", "EUR", "JPY" and "GBP"
    Then the values are accepted and stored for each currency

  @AC2
  Scenario: System‑wide default tolerance is applied when a currency has no explicit entry
    Given a tolerance store with a default tolerance of 0.5
    When the engine requests the tolerance for currency "CHF"
    Then the returned tolerance is 0.5

  @AC3
  Scenario: Only authorized users can create or edit tolerance records
    Given a tolerance store
    When user "ops_manager" sets tolerance for "USD" to 1.0
    Then the operation succeeds
    When user "regular_user" attempts to set tolerance for "EUR" to 0.8
    Then a permission error is raised

  @AC4
  Scenario: Invalid tolerance inputs are rejected
    Given a tolerance store
    When user "ops_manager" attempts to set tolerance for "JPY" to "-0.1"
    Then a validation error is raised
    When user "ops_manager" attempts to set tolerance for "JPY" to "high"
    Then a validation error is raised

  # ---------- Suppress Minor Breaks ----------
  @AC5
  Scenario: Engine retrieves the correct tolerance for a currency (or default)
    Given a tolerance store with default 0.5 and a tolerance of 1.0 for "USD"
    When the processor evaluates a break in currency "USD"
    Then the tolerance used is 1.0
    When the processor evaluates a break in currency "CHF"
    Then the tolerance used is 0.5

  @AC6
  Scenario: Breaks whose quantity and market‑value differences are within tolerance are suppressed
    Given a tolerance store with default 0.5 and a tolerance of 1.0 for "USD"
    And a break processor using that store
    When a break with id "BRK001", currency "USD", qty_diff 0.4, mv_diff 0.3 and both sources present is processed
    Then the break is marked as suppressed
    And no alert is generated for that break

  @AC7
  Scenario: Breaks that exist in only one source are never suppressed
    Given a tolerance store with default 0.5
    And a break processor using that store
    When a break with id "BRK002", currency "EUR", qty_diff 0.1, mv_diff 0.1, source_a_present true and source_b_present false is processed
    Then the break is not suppressed
    And an alert is generated for that break

  @AC8
  Scenario: Suppressed breaks are logged with reason “within tolerance”
    Given a tolerance store with default 0.5
    And a break processor using that store
    When a break with id "BRK003", currency "USD", qty_diff 0.2, mv_diff 0.2 and both sources present is processed
    Then the break is suppressed
    And the suppression log contains an entry for "BRK003" with reason "within tolerance"

  # ---------- Dashboard Break Alerts ----------
  @AC9
  Scenario: Alert entry is created when a break exceeds its currency tolerance
    Given a tolerance store with default 0.5 and a tolerance of 0.8 for "EUR"
    And a break processor using that store
    When a break with id "BRK004", currency "EUR", qty_diff 1.0, mv_diff 0.9 and both sources present is processed
    Then the break is not suppressed
    And an alert is created for "BRK004"

  @AC10
  Scenario: Alerts are read‑only
    Given a tolerance store with default 0.5
    And a break processor using that store
    When a break that generates an alert is processed
    Then the alert contains a flag "read_only" set to true

  @AC11
  Scenario: Alerts display currency, break amount and configured tolerance
    Given a tolerance store with default 0.5 and a tolerance of 1.2 for "GBP"
    And a break processor using that store
    When a break with id "BRK005", currency "GBP", qty_diff 2.0, mv_diff 1.5 and both sources present is processed
    Then the generated alert shows currency "GBP"
    And the alert shows qty_diff 2.0 and mv_diff 1.5
    And the alert shows tolerance 1.2

  @AC12
  Scenario: Alerts are refreshed in real time (or within 5 minutes)
    Given a tolerance store with default 0.5
    And a break processor using that store
    When a break that generates an alert is processed at time T0
    Then the alert appears in the dashboard feed within 5 minutes of T0

  # ---------- Audit Tolerance Changes ----------
  @AC13
  Scenario: Every create, update, or delete of a tolerance record writes an immutable audit entry
    Given a tolerance store
    When user "ops_manager" creates a tolerance for "CAD" with value 0.7
    And user "ops_manager" updates the tolerance for "CAD" to 0.9
    And user "ops_manager" deletes the tolerance for "CAD"
    Then the audit log contains three records with operation types "create", "update", "delete"
    And each record includes user_id, timestamp, operation type, currency, old_value and new_value

  @AC14
  Scenario: Audit table is append‑only; attempts to modify existing rows are rejected
    Given a tolerance store
    When an attempt is made to modify an existing audit record directly
    Then the operation is rejected (no method exists to modify audit rows)

  @AC15
  Scenario: Audit data is queryable and can be exported to CSV without loss of fidelity
    Given a tolerance store with at least one audit record
    When the audit log is queried
    Then the result can be written to a CSV file preserving all fields

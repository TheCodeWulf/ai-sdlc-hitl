Feature: Manage per‑currency FX break tolerances
  As an Operations manager and auditor I want a configurable, auditable
  tolerance table so that the reconciliation dashboard only shows material
  mismatches.

  # ----------------------------------------------------------------------
  # Acceptance Criterion 1 – CRUD of tolerance table
  # ----------------------------------------------------------------------
  @AC1
  Scenario: Create, edit and store per‑currency tolerance rows
    Given the tolerance configuration is empty
    When the Operations manager sets tolerance for currency "USD" to 100.0
    And the Operations manager sets tolerance for currency "EUR" to 80.0
    Then the configuration should contain a tolerance for "USD" equal to 100.0
    And the configuration should contain a tolerance for "EUR" equal to 80.0
    And the audit log should contain a create entry for user "ops_manager" and currency "USD"
    And the audit log should contain a create entry for user "ops_manager" and currency "EUR"

  # ----------------------------------------------------------------------
  # Acceptance Criterion 2 – In‑tolerance breaks are hidden
  # ----------------------------------------------------------------------
  @AC2
  Scenario: Break within tolerance is excluded from the manager’s queue
    Given a tolerance of 50.0 is set for currency "JPY"
    When a break of amount 30.0 for currency "JPY" is evaluated
    Then the break is considered "in tolerance"
    And the break appears in the list of in‑tolerance breaks
    And no alert is generated for this break

  # ----------------------------------------------------------------------
  # Acceptance Criterion 3 – Default tolerance fallback
  # ----------------------------------------------------------------------
  @AC3
  Scenario: System uses default tolerance when no explicit currency entry exists
    Given the default tolerance is set to 20.0 by user "admin"
    And no specific tolerance exists for currency "GBP"
    When a break of amount 15.0 for currency "GBP" is evaluated
    Then the break is considered "in tolerance"
    And the tolerance used is 20.0
    When a break of amount 25.0 for currency "GBP" is evaluated
    Then the break is considered "out of tolerance"
    And the tolerance used is 20.0

  # ----------------------------------------------------------------------
  # Acceptance Criterion 4 – Out‑of‑tolerance alerts are read‑only
  # ----------------------------------------------------------------------
  @AC4
  Scenario: Out‑of‑tolerance break triggers a read‑only alert on the dashboard
    Given a tolerance of 10.0 is set for currency "USD"
    When a break of amount 12.5 for currency "USD" is evaluated
    Then the break is considered "out of tolerance"
    And a read‑only alert is generated containing:
      | currency | break_amount | tolerance |
      | USD      | 12.5         | 10.0      |

  # ----------------------------------------------------------------------
  # Acceptance Criterion 5 – Immutable audit log of tolerance changes
  # ----------------------------------------------------------------------
  @AC5
  Scenario: Every create or update writes an append‑only audit entry
    When user "alice" sets the default tolerance to 5.0
    And user "bob" creates a tolerance of 0.0 for currency "EUR"
    And user "bob" updates the tolerance for currency "EUR" to 7.0
    Then the audit log contains an entry for user "alice" with currency "DEFAULT", old value 0.0 and new value 5.0
    And the audit log contains an entry for user "bob" with currency "EUR", old value 0.0 and new value 7.0
    And the audit log file cannot be modified through the public API

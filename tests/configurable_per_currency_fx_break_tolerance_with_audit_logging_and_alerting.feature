Feature: Configurable per‑currency FX break tolerance with audit logging and alerting
  As an Ops manager, reconciliation system, manager and auditor
  I want to be able to define, use and audit FX break tolerances per currency
  So that only material breaks surface and all changes are traceable.

  # --------------------------------------------------------------------
  # Story 1 – Define Currency Tolerances
  # --------------------------------------------------------------------
  @AC1_CreateEditDelete
  Scenario: Admin can create, update and delete tolerance entries via the API
    Given an empty tolerance manager
    When the user "alice" with role "OpsManager" creates a tolerance for currency "USD" with value 0.05
    And the user "alice" with role "OpsManager" creates a tolerance for currency "EUR" with value 0.03
    And the user "alice" with role "OpsManager" creates a tolerance for currency "JPY" with value 0.01
    Then the tolerance table contains entries for "USD", "EUR" and "JPY"
    When the user "bob" with role "OpsManager" updates the tolerance for currency "EUR" to value 0.04
    Then the tolerance for "EUR" is 0.04
    When the user "bob" with role "OpsManager" deletes the tolerance for currency "JPY"
    Then the tolerance for "JPY" falls back to the default tolerance

  @AC1_DefaultFallback
  Scenario: System falls back to a configurable default tolerance when a currency is missing
    Given a tolerance manager with default tolerance 0.02
    And a tolerance entry for currency "USD" with value 0.05
    When the system requests the tolerance for currency "CAD"
    Then the returned tolerance is 0.02

  @AC1_InvalidValues
  Scenario: Invalid tolerance values are rejected with a clear error
    Given an empty tolerance manager
    When the user "charlie" with role "OpsManager" attempts to create a tolerance for currency "GBP" with value -0.01
    Then a "ToleranceError" is raised with message containing "non‑negative"
    When the user "charlie" with role "OpsManager" attempts to create a tolerance for currency "GBP" with value "abc"
    Then a "ToleranceError" is raised with message containing "non‑negative"

  @AC1_AuditLog
  Scenario: Every change to the tolerance table is recorded in an immutable audit log
    Given an empty tolerance manager and a fresh audit log
    When the user "dana" with role "OpsManager" creates a tolerance for currency "USD" with value 0.05
    And the user "dana" with role "OpsManager" updates the tolerance for currency "USD" to value 0.06
    And the user "dana" with role "OpsManager" deletes the tolerance for currency "USD"
    Then the audit log contains three entries in order:
      | operation | currency | old_value | new_value |
      | create    | USD      |           | 0.05      |
      | update    | USD      | 0.05      | 0.06      |
      | delete    | USD      | 0.06      |           |
    And each entry records user id "dana" and role "OpsManager"

  # --------------------------------------------------------------------
  # Story 2 – Suppress Tolerable Breaks
  # --------------------------------------------------------------------
  @AC2_SuppressionLogic
  Scenario: Breaks within tolerance are suppressed and logged
    Given a tolerance manager with entry "EUR" = 0.03
    And a logger that records suppression events
    And a break processor using the above manager, an alert manager and the logger
    When the processor evaluates a break with:
      | currency      | EUR |
      | qty_diff      | 0.02 |
      | mv_diff       | 0.01 |
      | source_missing| false |
      | user_id       | "recon1" |
    Then the break is suppressed
    And a suppression log entry exists containing currency "EUR", difference "0.02", tolerance "0.03"

  @AC2_MissingSourceNeverSuppressed
  Scenario: Breaks that exist in only one source are never suppressed
    Given a tolerance manager with entry "USD" = 0.05
    And a break processor
    When the processor evaluates a break with:
      | currency      | USD |
      | qty_diff      | 0.01 |
      | mv_diff       | 0.01 |
      | source_missing| true |
      | user_id       | "recon2" |
    Then the break is not suppressed
    And no alert is generated

  @AC2_NoImpactOnNAV
  Scenario: Suppression does not alter NAV or valuation calculations
    # This is a non‑functional requirement – verified by ensuring the processor
    # returns only suppression/alert flags and does not expose any NAV mutation.
    Given a tolerance manager with entry "JPY" = 0.02
    And a break processor
    When the processor evaluates a break with qty_diff 0.015 and mv_diff 0.015 for currency "JPY"
    Then the result indicates suppression = true
    And the processor does not modify any external NAV state (implicit by design)

  # --------------------------------------------------------------------
  # Story 3 – Alert on Excess Breaks
  # --------------------------------------------------------------------
  @AC3_AlertGeneration
  Scenario: An alert is generated when a break exceeds its tolerance
    Given a tolerance manager with entry "USD" = 0.04
    And an empty alert manager
    And a break processor
    When the processor evaluates a break with:
      | currency      | USD |
      | qty_diff      | 0.10 |
      | mv_diff       | 0.08 |
      | source_missing| false |
      | user_id       | "recon3" |
    Then the break is not suppressed
    And an alert is created with currency "USD", break_amount "0.10", tolerance "0.04"

  @AC3_AlertPersistenceAndSearch
  Scenario: Alerts persist for at least 30 days and can be searched
    Given an alert manager
    And an alert created 10 days ago for currency "EUR"
    And an alert created 31 days ago for currency "EUR"
    When searching alerts for currency "EUR"
    Then only the recent alert is returned
    And the returned alert is at most 30 days old

  @AC3_NoAlertForSuppressedBreaks
  Scenario: No alert is emitted for breaks that are suppressed by tolerance
    Given a tolerance manager with entry "GBP" = 0.05
    And a break processor
    When the processor evaluates a break with:
      | currency      | GBP |
      | qty_diff      | 0.03 |
      | mv_diff       | 0.02 |
      | source_missing| false |
      | user_id       | "recon4" |
    Then the break is suppressed
    And no alert is generated

  # --------------------------------------------------------------------
  # Story 4 – Audit Tolerance Changes
  # --------------------------------------------------------------------
  @AC4_ImmutableAuditLog
  Scenario: Audit log entries cannot be altered or deleted
    Given an audit log with several entries
    When an attempt is made to remove an entry from the audit log
    Then a "AttributeError" (or similar) is raised because the log is read‑only

  @AC4_ExportCsv
  Scenario: Audit log can be exported to CSV for compliance review
    Given an audit log containing at least one entry
    When the log is exported to file "audit_export.csv"
    Then a CSV file "audit_export.csv" exists with a header row and the entry data

  @AC4_AccessControl
  Scenario: Only users with the “Auditor” role can read the audit log
    Given an audit log
    When a user with role "OpsManager" attempts to read the audit log
    Then access is denied (implementation placeholder)
    When a user with role "Auditor" reads the audit log
    Then the full list of entries is returned

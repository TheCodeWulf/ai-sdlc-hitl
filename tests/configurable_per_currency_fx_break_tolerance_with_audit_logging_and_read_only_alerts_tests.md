**Feature: FX Tolerance Service**

```gherkin
Feature: Configurable per‑currency FX break tolerance with audit logging and read‑only alerts
  As an Ops manager, system, compliance officer and manager
  I want to manage FX tolerances, suppress minor breaks, keep an immutable audit log
  and receive read‑only alerts for material breaks.

  # ----------------------------------------------------------------------
  # Story 1 – Define Currency Tolerances
  # ----------------------------------------------------------------------
  @AC1.1
  Scenario: Store a numeric tolerance for a currency via the API
    Given the service is running
    When I send a PUT request to "/tolerances/USD" with payload:
      """
      {
        "tolerance": 0.025,
        "user_id": "ops_user"
      }
      """
    Then the response status code is 200
    And the response body contains "currency": "USD"
    And the response body contains "tolerance": 0.025
    And the response body contains "effective_tolerance": 0.025

  @AC1.2
  Scenario: Fallback to the configured default tolerance when a currency is absent
    Given the service is running
    And the default tolerance is set to 0.01
    When I send a GET request to "/tolerances/JPY"
    Then the response status code is 200
    And the response body contains "currency": "JPY"
    And the response body contains "tolerance": 0.01
    And the response body contains "effective_tolerance": 0.01

  @AC1.3
  Scenario: API returns 400 for an invalid currency code
    Given the service is running
    When I send a PUT request to "/tolerances/USDX" with payload:
      """
      {
        "tolerance": 0.02,
        "user_id": "ops_user"
      }
      """
    Then the response status code is 400
    And the response body contains "Invalid currency code"

  @AC1.3
  Scenario: API returns 400 for a non‑numeric tolerance value
    Given the service is running
    When I send a PUT request to "/tolerances/EUR" with payload:
      """
      {
        "tolerance": "high",
        "user_id": "ops_user"
      }
      """
    Then the response status code is 400
    And the response body contains "value is not a valid float"

  @AC1.4
  Scenario: Tolerance change is persisted within 5 seconds
    Given the service is running
    When I send a PUT request to "/tolerances/EUR" with payload:
      """
      {
        "tolerance": 0.015,
        "user_id": "ops_user"
      }
      """
    Then the response status code is 200
    When I wait 5 seconds
    And I send a GET request to "/tolerances/EUR"
    Then the response body contains "tolerance": 0.015

  @AC1.5
  Scenario: Response includes the effective tolerance used for break calculations
    Given the service is running
    When I send a PUT request to "/tolerances/GBP" with payload:
      """
      {
        "tolerance": 0.03,
        "user_id": "ops_user"
      }
      """
    Then the response body contains "effective_tolerance": 0.03

  # ----------------------------------------------------------------------
  # Story 2 – Suppress Minor Breaks
  # ----------------------------------------------------------------------
  @AC2.1
  Scenario: Engine compares quantity and market‑value differences against the applicable tolerance
    Given the service is running
    And a tolerance of 0.02 is set for currency "USD"
    When I POST to "/breaks/evaluate" with body:
      """
      [
        {"currency":"USD","quantity_diff":0.015,"market_value_diff":0.018}
      ]
      """
    Then the response contains an empty "material_breaks" list

  @AC2.2
  Scenario: Breaks with both differences ≤ tolerance are suppressed
    Given the service is running
    And a tolerance of 0.01 is set for currency "EUR"
    When I POST to "/breaks/evaluate" with body:
      """
      [
        {"currency":"EUR","quantity_diff":0.008,"market_value_diff":0.009}
      ]
      """
    Then the response contains an empty "material_breaks" list

  @AC2.3
  Scenario: Breaks where either difference > tolerance remain visible and are marked material
    Given the service is running
    And a tolerance of 0.01 is set for currency "JPY"
    When I POST to "/breaks/evaluate" with body:
      """
      [
        {"currency":"JPY","quantity_diff":0.012,"market_value_diff":0.005}
      ]
      """
    Then the response "material_breaks" contains an entry with:
      | currency | break_amount |
      | JPY      | 0.012         |

  @AC2.4
  Scenario: Suppression does not affect position‑only mismatches
    Given the service is running
    And a tolerance of 0.01 is set for currency "USD"
    When I POST to "/breaks/evaluate" with body:
      """
      [
        {"currency":"USD","quantity_diff":0.0,"market_value_diff":0.0}
      ]
      """
    Then the response "material_breaks" is empty
    And no alert is created for a position‑only mismatch (i.e., a record with missing counterpart)

  @AC2.5
  Scenario: Unit‑test suite validates suppression for USD, EUR, JPY and default tolerance
    Given the test suite includes cases for currencies "USD", "EUR", "JPY" and an unknown currency
    When the suite runs the evaluate_breaks endpoint
    Then all assertions about suppression and material break detection pass

  # ----------------------------------------------------------------------
  # Story 3 – Audit Tolerance Changes
  # ----------------------------------------------------------------------
  @AC3.1
  Scenario: Successful tolerance change creates an immutable audit log entry
    Given the service is running
    When I send a PUT request to "/tolerances/USD" with payload:
      """
      {
        "tolerance": 0.02,
        "user_id": "audit_user"
      }
      """
    Then an audit entry exists with:
      | currency | new_tolerance | user_id    | operation |
      | USD      | 0.02          | audit_user | upsert    |

  @AC3.2
  Scenario: Audit log primary key cannot be altered or deleted
    Given the service is running
    When I attempt to DELETE "/audit/1"
    Then the response status code is 404
    And the audit entry with id 1 still exists

  @AC3.3
  Scenario: Retrieval API returns entries filtered by currency and date range, ordered chronologically
    Given the service is running
    And audit entries exist for currencies "USD" and "EUR" on different timestamps
    When I GET "/audit?currency=USD&start=2024-01-01T00:00:00Z&end=2024-12-31T23:59:59Z"
    Then the response contains only entries with currency "USD"
    And the entries are ordered by "ts" ascending

  @AC3.4
  Scenario: Attempting to modify an existing audit entry results in a 403 error
    Given the service is running
    When I send a PATCH request to "/audit/1" with body:
      """
      {"new_tolerance": 0.03}
      """
    Then the response status code is 403

  @AC3.5
  Scenario: Log retention configuration archives old entries but keeps them queryable
    Given the service is running
    And the audit retention period is set to 1 day
    And an audit entry older than 2 days exists
    When the daily retention job runs
    Then the old entry is moved to the archive store
    And a GET "/audit" still returns the archived entry

  # ----------------------------------------------------------------------
  # Story 4 – Read‑Only Alerting
  # ----------------------------------------------------------------------
  @AC4.1
  Scenario: Material break creates a read‑only alert attached to the manager’s dashboard
    Given the service is running
    And a tolerance of 0.01 is set for currency "USD"
    When I POST to "/breaks/evaluate" with body:
      """
      [
        {"currency":"USD","quantity_diff":0.02,"market_value_diff":0.005}
      ]
      """
    Then an alert exists with:
      | currency | break_amount | tolerance |
      | USD      | 0.02         | 0.01      |

  @AC4.2
  Scenario: Alerts are displayed in a read‑only panel with no action buttons
    Given the service is running
    When I GET "/alerts"
    Then each returned alert contains fields "id", "currency", "break_amount", "tolerance", "ts", "cleared"
    And there is no endpoint to modify an alert other than clearing it

  @AC4.3
  Scenario: Alert shows currency, break amount, tolerance value and timestamp
    Given the service is running
    When I GET "/alerts"
    Then each alert includes the correct "currency", "break_amount", "tolerance" and a recent "ts"

  @AC4.4
  Scenario: Alerts persist until manually cleared
    Given the service is running
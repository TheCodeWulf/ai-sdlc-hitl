Feature: FX tolerance configuration, suppression and read‑only alerts

  # --------------------------------------------------------------------
  # Story 1 – Configure Currency Tolerances
  # --------------------------------------------------------------------
  @AC1
  Scenario: Ops manager can set tolerance for supported currencies and a default
    Given the service is running
    And a user with role "Tolerance-Editor" and id "alice"
    When the user sets tolerance "0.5" for currency "USD"
    Then the response status is 200
    And the tolerance for "USD" is stored as "0.5"

    When the user sets tolerance "0.3" for currency "EUR"
    Then the response status is 200
    And the tolerance for "EUR" is stored as "0.3"

    When the user sets tolerance "0.2" for currency "JPY"
    Then the response status is 200
    And the tolerance for "JPY" is stored as "0.2"

    When the user sets tolerance "0.1" for currency "DEFAULT"
    Then the response status is 200
    And the tolerance for "DEFAULT" is stored as "0.1"

  @AC2
  Scenario: Only users with the Tolerance‑Editor role can modify tolerances
    Given the service is running
    And a user with role "Dashboard-Viewer" and id "bob"
    When the user attempts to set tolerance "0.4" for currency "USD"
    Then the response status is 403
    And the tolerance for "USD" remains unchanged

  @AC4
  Scenario: System rejects negative tolerance values
    Given the service is running
    And a user with role "Tolerance-Editor" and id "alice"
    When the user attempts to set tolerance "-0.5" for currency "USD"
    Then the response status is 400
    And the response contains "Tolerance must be a non‑negative number"

  @AC5
  Scenario: Updated tolerance is used by subsequent break processing
    Given the service is running
    And a user with role "Tolerance-Editor" and id "alice"
    And the tolerance for "USD" is set to "0.2"
    When a break with id "BRK001", currency "USD", qty_diff "0.15", mv_diff "0.10", missing false is ingested
    Then the break is suppressed
    When the tolerance for "USD" is updated to "0.05"
    And a break with id "BRK002", currency "USD", qty_diff "0.04", mv_diff "0.03", missing false is ingested
    Then the break is suppressed
    When a break with id "BRK003", currency "USD", qty_diff "0.06", mv_diff "0.02", missing false is ingested
    Then the break generates a read‑only alert

  # --------------------------------------------------------------------
  # Story 2 – Suppress In‑Tolerance Breaks
  # --------------------------------------------------------------------
  @AC2_Story2
  Scenario: Breaks within tolerance are suppressed and logged
    Given the service is running
    And the tolerance for "EUR" is set to "0.5"
    When a break with id "BRK100", currency "EUR", qty_diff "0.3", mv_diff "0.4", missing false is ingested
    Then the break is suppressed
    And a suppressed‑log entry exists for break "BRK100" with tolerance "0.5"

  @AC3_Story2
  Scenario: Missing position breaks are never suppressed
    Given the service is running
    And the tolerance for "JPY" is set to "1.0"
    When a break with id "BRK200", currency "JPY", qty_diff "0.2", mv_diff "0.2", missing true is ingested
    Then the break is not suppressed
    And no suppressed‑log entry exists for break "BRK200"

  @AC5_Story2
  Scenario: Suppression applies only to quantity and market‑value differences
    Given the service is running
    And the tolerance for "USD" is set to "0.5"
    When a break with id "BRK300", currency "USD", qty_diff "0.6", mv_diff "0.4", missing false is ingested
    Then the break generates a read‑only alert
    # (the qty difference exceeds tolerance while mv does not – still an alert)

  # --------------------------------------------------------------------
  # Story 3 – Read‑Only Alert for Out‑of‑Tolerance Breaks
  # --------------------------------------------------------------------
  @AC1_Story3
  Scenario: Out‑of‑tolerance break creates a read‑only alert
    Given the service is running
    And the tolerance for "USD" is set to "0.2"
    When a break with id "BRK400", currency "USD", qty_diff "0.3", mv_diff "0.1", missing false is ingested
    Then the break generates a read‑only alert
    And the alert for break "BRK400" contains currency "USD", break_amount "0.3", tolerance "0.2"

  @AC3_Story3
  Scenario: Dashboard‑Viewer can view alerts but cannot modify them
    Given the service is running
    And a user with role "Dashboard-Viewer" and id "carol"
    When the user requests the alerts list
    Then the response status is 200
    And the returned alerts are read‑only (no delete endpoint is available)

  @AC5_Story3
  Scenario: Alerts persist for at least 30 days and are searchable
    Given the service is running
    And the tolerance for "EUR" is set to "0.1"
    When a break with id "BRK500", currency "EUR", qty_diff "0.2", mv_diff "0.05", missing false is ingested
    Then the break generates a read‑only alert
    When the user with role "Dashboard-Viewer" queries alerts with currency "EUR" and min_amount "0.15"
    Then the response contains an alert for break "BRK500"
    And the alert's expires_at is at least 30 days from now

  # --------------------------------------------------------------------
  # Story 4 – Audit Tolerance Changes
  # --------------------------------------------------------------------
  @AC1_Story4
  Scenario: Every tolerance change creates an audit log entry
    Given the service is running
    And a user with role "Tolerance-Editor" and id "dave"
    When the user sets tolerance "0.25" for currency "USD"
    Then an audit entry exists with user_id "dave", operation "CREATE", currency "USD", old_value null, new_value "0.25"

    When the user updates tolerance to "0.30" for currency "USD"
    Then an audit entry exists with user_id "dave", operation "UPDATE", currency "USD", old_value "0.25", new_value "0.30"

  @AC3_Story4
  Scenario: Audit log can be queried with filters
    Given the service is running
    And a user with role "Audit-Viewer" and id "erin"
    When the user queries the audit endpoint with currency "USD"
    Then the response includes all audit entries for currency "USD"

  @AC4_Story4
  Scenario: Only Audit‑Viewer role can query the audit log
    Given the service is running
    And a user with role "Dashboard-Viewer" and id "frank"
    When the user queries the audit endpoint
    Then the response status is 403

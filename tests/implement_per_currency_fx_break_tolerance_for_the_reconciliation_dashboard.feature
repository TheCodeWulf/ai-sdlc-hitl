Feature: Per‑currency FX break tolerance for the reconciliation dashboard

  @AC1
  Scenario: Manager can set numeric tolerance values for USD, EUR and JPY
    Given the configuration page is displayed
    When the manager enters "0.02" for USD, "0.015" for EUR and "0.03" for JPY
    And clicks the Save button
    Then the system should store the values 0.02, 0.015 and 0.03 in the database

  @AC2
  Scenario: Default tolerance is applied to unspecified currencies
    Given the system has no tolerance entry for GBP
    When the system retrieves the tolerance for GBP
    Then the returned tolerance should be the default value 0.01

  @AC3
  Scenario: Tolerance values survive system restarts
    Given the manager has set USD tolerance to 0.02
    When the application is restarted
    And the manager navigates to the configuration page
    Then the displayed USD tolerance should be 0.02

  @AC4
  Scenario: Current tolerance values are displayed in the dashboard configuration panel
    Given the database contains USD=0.02, EUR=0.015, JPY=0.03
    When the manager opens the configuration page
    Then the page should show 0.02 for USD, 0.015 for EUR and 0.03 for JPY

  @AC5
  Scenario: Alert is generated when a break exceeds tolerance
    Given a break with quantity difference 0.05 in USD
    And the USD tolerance is set to 0.04
    When the system processes the break
    Then an alert should be created for this break

  @AC6
  Scenario: Alert displays currency, break details and exceeded tolerance
    Given an alert exists for a USD break with difference 0.05
    When the manager views the alert
    Then the alert should show currency USD, difference 0.05 and threshold 0.04

  @AC7
  Scenario: Alerts remain visible until acknowledged
    Given an alert is present on the manager’s dashboard
    When the manager acknowledges the alert
    Then the alert should disappear from the dashboard

  @AC8
  Scenario: No automatic action is taken on the book when an alert is generated
    Given a break exceeds tolerance
    When the alert is generated
    Then the underlying book record should remain unresolved

  @AC9
  Scenario: Audit record is created on tolerance change
    Given the manager changes USD tolerance from 0.02 to 0.025
    When the change is committed
    Then an audit record should exist with user ID, timestamp, currency USD, old value 0.02 and new value 0.025

  @AC10
  Scenario: Audit records are immutable and append‑only
    Given an audit record exists for a tolerance change
    When the system attempts to modify that record
    Then the modification should be rejected and the record unchanged

  @AC11
  Scenario: Audit log can be queried by date range and user
    Given audit records exist for user "alice" on 2024‑01‑10 and 2024‑01‑12
    When the system queries the audit log for user "alice" between 2024‑01‑09 and 2024‑01‑13
    Then the query should return both audit records

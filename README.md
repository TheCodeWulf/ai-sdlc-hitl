# AI-Powered SDLC with Human-in-the-Loop (HITL)

An agent runs **every phase** of the software delivery lifecycle — from a meeting
transcript to a merged pull request — and a **human approves at every gate**. The AI
drafts and checks; it never merges or ships on its own.

The lifecycle is the constant; the tool integrations are pluggable.

```
Teams/Zoom transcript
  1. Intake      transcript  → project brief                 [human gate]
  2. PO/Backlog  brief       → epics & stories → JIRA         [human gate]
  3. Code        stories     → implementation → GitHub PR     [human gate]
  4. Test        code        → tests                          [human gate]
  5. PR Review   PR          → SAST + AI review findings      [human gate]
  6. Release     approved PR → merge & check-in               [human gate]
```

> **Synthetic demo app.** The "product" being built here is a representative
> **reconciliation service** (`app/`) — dual-source position records, breaks, and
> per-currency tolerances. It mirrors the *shape* of a fund-administration problem;
> it is not any proprietary platform and contains no client data.

## Layout
```
app/           the product under development (reconciliation service)
tests/         its tests
agents/        the six SDLC agents (added incrementally)
integrations/  the tool "hands": github_client.py, jira_client.py
demo/          the meeting transcript that seeds the flow
```

## Design principles
- **High cohesion** — each agent owns exactly one phase.
- **Low coupling** — phases hand off via artifacts; integrations attach *after* a
  phase's human gate and are optional (a missing token never breaks the flow).
- **No lock-in** — the model provider is one config line (Groq today; Azure AI
  Foundry in production). SAST is pluggable (CodeQL → Checkmarx / Snyk).

## Quick start
```bash
python -m venv .venv && .venv\Scripts\Activate.ps1     # Windows
pip install -r requirements.txt
cp .env.example .env        # fill in GROQ / GITHUB / JIRA values
pytest tests/ -q            # the app's tests

# rehearse the integrations with NO tokens (dry-run):
python integrations/jira_client.py
python integrations/github_client.py
```

## Security gate (PR Review phase)
Layered, and pluggable:
- **SAST** — CodeQL here; Checkmarx / Snyk in your environment.
- **DAST** — OWASP ZAP / Checkmarx DAST, post-deploy (named, not run here).
- **AI review** — reasons about logic & context, posts findings on the PR.
- **HITL** — a human reviews all findings and approves before merge.

## Human-in-the-Loop
Every phase pauses for a person to **approve / edit / reject**. In production these map
1:1 to your systems of record: Jira issues and GitHub PR approvals — the audit trail is
the trail of human-approved artifacts.

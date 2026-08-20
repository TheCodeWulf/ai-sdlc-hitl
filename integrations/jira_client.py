"""
jira_client.py — the Jira "hand" for the SDLC pipeline.

Plugs into the PO/Backlog phase WITHOUT changing the flow:
  * PO phase (after its human gate) -> create_epic_and_stories(...)
    creates a REAL Jira Epic and its Stories in your project.

Config (from .env):
  JIRA_SITE=https://your-site.atlassian.net
  JIRA_EMAIL=you@example.com
  JIRA_TOKEN=your_api_token          # id.atlassian.com/manage-profile/security/api-tokens
  JIRA_PROJECT=REC                   # the project key

Dry-run: set JIRA_DRY_RUN=1 (or pass dry_run=True) to print actions without calling
the API — used to rehearse/verify safely with no token.
"""
from __future__ import annotations
import base64
import json
import os
import urllib.request
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


@dataclass
class BacklogItem:
    """The structured backlog the PO agent produces (kept model-agnostic)."""
    epic: str
    stories: list[str] = field(default_factory=list)


@dataclass
class JiraResult:
    epic_key: str
    story_keys: list[str]
    dry_run: bool


class JiraClient:
    def __init__(self, site=None, email=None, token=None, project=None, dry_run=None):
        self.site = (site or os.getenv("JIRA_SITE", "")).rstrip("/")
        self.email = email or os.getenv("JIRA_EMAIL", "")
        self.token = token or os.getenv("JIRA_TOKEN", "")
        self.project = project or os.getenv("JIRA_PROJECT", "")
        env_dry = os.getenv("JIRA_DRY_RUN", "").strip() in ("1", "true", "True")
        self.dry_run = env_dry if dry_run is None else dry_run
        if not self.dry_run and not all([self.site, self.email, self.token, self.project]):
            raise SystemExit("Set JIRA_SITE, JIRA_EMAIL, JIRA_TOKEN, JIRA_PROJECT in .env (or use dry_run).")

    # ---- low-level REST helper --------------------------------------------
    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.site}/rest/api/3/{path}"
        data = json.dumps(payload).encode()
        auth = base64.b64encode(f"{self.email}:{self.token}".encode()).decode()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Basic {auth}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode())

    def _create_issue(self, summary: str, issue_type: str, parent_key: str | None = None) -> str:
        fields = {
            "project": {"key": self.project},
            "summary": summary[:250],
            "issuetype": {"name": issue_type},
        }
        if parent_key:  # link a Story under an Epic (Jira Cloud: parent field)
            fields["parent"] = {"key": parent_key}
        res = self._post("issue", {"fields": fields})
        return res["key"]

    # ---- PO phase: create the epic + stories ------------------------------
    def create_epic_and_stories(self, item: BacklogItem) -> JiraResult:
        if self.dry_run:
            print(f"[dry-run] create Epic in {self.project or 'PROJECT'}: {item.epic}")
            keys = []
            for i, s in enumerate(item.stories, 1):
                k = f"{self.project or 'REC'}-{100+i}"
                print(f"[dry-run]   create Story {k}: {s}")
                keys.append(k)
            return JiraResult(epic_key=f"{self.project or 'REC'}-100", story_keys=keys, dry_run=True)

        epic_key = self._create_issue(item.epic, "Epic")
        story_keys = [self._create_issue(s, "Story", parent_key=epic_key) for s in item.stories]
        return JiraResult(epic_key=epic_key, story_keys=story_keys, dry_run=False)


# --------------------------------------------------------------------------
# Self-check / rehearsal: `python integrations/jira_client.py`  (dry-run, no token)
# --------------------------------------------------------------------------
if __name__ == "__main__":
    os.environ.setdefault("JIRA_DRY_RUN", "1")
    jira = JiraClient(dry_run=True)
    item = BacklogItem(
        epic="Reduce reconciliation noise with per-currency break tolerances",
        stories=[
            "As an ops analyst, I want per-currency tolerances so sub-threshold FX breaks are suppressed",
            "As a manager, I want an alert when a break exceeds its currency's tolerance",
            "As an auditor, I want an append-only record of who set each tolerance and when",
        ],
    )
    res = jira.create_epic_and_stories(item)
    print("\nEpic:", res.epic_key, "| Stories:", res.story_keys)

"""
jira_client.py - Jira "hand" for the SDLC pipeline (team-managed friendly).

Creates a real Epic and links Stories to it via the parent field (works for
team-managed Jira Cloud projects like AIS). Dry-run safe.

.env:
  JIRA_SITE=https://oshuklaiim.atlassian.net
  JIRA_EMAIL=you@example.com
  JIRA_TOKEN=your_api_token
  JIRA_PROJECT=AIS
  # JIRA_DRY_RUN=1   # rehearse with no calls
"""
from __future__ import annotations
import base64, json, os, urllib.request, urllib.error
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv; load_dotenv()
except Exception:
    pass


@dataclass
class BacklogItem:
    epic: str
    stories: list[str] = field(default_factory=list)


@dataclass
class JiraResult:
    epic_key: str
    story_keys: list[str]
    dry_run: bool
    url: str = ""


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

    def _post(self, path, payload):
        url = f"{self.site}/rest/api/3/{path}"
        data = json.dumps(payload).encode()
        auth = base64.b64encode(f"{self.email}:{self.token}".encode()).decode()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Basic {auth}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode()
            raise RuntimeError(f"Jira {e.code}: {detail}") from None

    def _create(self, summary, issue_type, parent_key=None):
        fields = {
            "project": {"key": self.project},
            "summary": summary[:250],
            "issuetype": {"name": issue_type},
        }
        if parent_key:
            fields["parent"] = {"key": parent_key}   # team-managed: Story -> Epic via parent
        return self._post("issue", {"fields": fields})["key"]

    def create_epic_and_stories(self, item: BacklogItem) -> JiraResult:
        if self.dry_run:
            print(f"[dry-run] create Epic in {self.project or 'AIS'}: {item.epic}")
            keys = []
            for i, s in enumerate(item.stories, 1):
                k = f"{self.project or 'AIS'}-{100+i}"
                print(f"[dry-run]   create Story {k}: {s}")
                keys.append(k)
            return JiraResult(f"{self.project or 'AIS'}-100", keys, True)

        epic_key = self._create(item.epic, "Epic")
        story_keys = []
        for s in item.stories:
            try:
                story_keys.append(self._create(s, "Story", parent_key=epic_key))
            except RuntimeError as e:
                # if parent linking is rejected, create the story unparented rather than fail
                print(f"   (story parent link failed, creating unlinked: {e})")
                story_keys.append(self._create(s, "Story"))
        return JiraResult(epic_key, story_keys, False, url=f"{self.site}/browse/{epic_key}")


if __name__ == "__main__":
    os.environ.setdefault("JIRA_DRY_RUN", "1")
    jira = JiraClient(dry_run=True)
    res = jira.create_epic_and_stories(BacklogItem(
        epic="Per-currency FX break tolerance with audit and alerts",
        stories=["Configure Tolerances", "Suppress Minor Breaks", "Alert Material Breaks", "Audit Log of Changes"],
    ))
    print("\nEpic:", res.epic_key, "| Stories:", res.story_keys)

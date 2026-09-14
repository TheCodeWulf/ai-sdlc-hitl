"""
github_client.py — the GitHub "hand" for the SDLC pipeline.

It plugs into the existing phases WITHOUT changing the flow:
  * Code phase (after its human gate)   -> open_pr(...)      opens a REAL pull request
  * PR Review phase                     -> post_findings(...) posts the AI review on the PR
  * HITL approval                       -> merge_pr(...)      merges & checks in

Config (from .env):
  GITHUB_TOKEN=ghp_...                 # classic PAT with 'repo' scope
  GITHUB_REPO=Thecodewulf/your-repo    # owner/name

Dry-run: set GITHUB_DRY_RUN=1 (or pass dry_run=True) to print actions without calling
the API — used to rehearse/verify safely with no token.
"""
from __future__ import annotations
import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


@dataclass
class PRResult:
    number: int | None
    url: str
    branch: str
    dry_run: bool


class GitHubClient:
    def __init__(self, token: str | None = None, repo: str | None = None, dry_run: bool | None = None):
        self.repo_full = repo or os.getenv("GITHUB_REPO", "")
        self.token = token or os.getenv("GITHUB_TOKEN", "")
        env_dry = os.getenv("GITHUB_DRY_RUN", "").strip() in ("1", "true", "True")
        self.dry_run = env_dry if dry_run is None else dry_run
        self._repo = None
        if not self.dry_run:
            if not self.token or not self.repo_full:
                raise SystemExit("Set GITHUB_TOKEN and GITHUB_REPO in .env (or use dry_run).")
            from github import Github, Auth
            gh = Github(auth=Auth.Token(self.token))
            self._repo = gh.get_repo(self.repo_full)

    # ---- Code phase: open a real PR with the generated change ---------------
    def open_pr(self, branch: str, title: str, body: str,
                files: dict[str, str], base: str = "main") -> PRResult:
        """Create `branch` off `base`, commit `files` (path -> content), open a PR."""
        if self.dry_run:
            print(f"[dry-run] branch '{branch}' off '{base}'")
            for p in files:
                print(f"[dry-run]   commit {p} ({len(files[p])} bytes)")
            print(f"[dry-run] open PR: {title}")
            return PRResult(number=None, url=f"(dry-run) {self.repo_full}#PR", branch=branch, dry_run=True)

        repo = self._repo
        base_sha = repo.get_branch(base).commit.sha
        # create the feature branch (ignore if it already exists)
        try:
            repo.create_git_ref(ref=f"refs/heads/{branch}", sha=base_sha)
        except Exception:
            pass
        # commit each file (create or update)
        for path, content in files.items():
            try:
                existing = repo.get_contents(path, ref=branch)
                repo.update_file(path, f"agent: update {path}", content, existing.sha, branch=branch)
            except Exception:
                repo.create_file(path, f"agent: add {path}", content, branch=branch)
        pr = repo.create_pull(title=title, body=body, head=branch, base=base)
        return PRResult(number=pr.number, url=pr.html_url, branch=branch, dry_run=False)

    # ---- PR Review phase: post the AI review + SAST findings on the PR ------
    def post_findings(self, pr_number: int | None, summary: str,
                      request_changes: bool = False) -> None:
        """Post the review agent's findings on the PR.

        Note: GitHub forbids REQUEST_CHANGES / APPROVE reviews on your OWN PR, so we
        try a COMMENT review first and fall back to a normal PR comment (always allowed).
        The verdict is prefixed into the text either way.
        """
        event = "REQUEST_CHANGES" if request_changes else "COMMENT"
        verdict = "REQUEST CHANGES" if request_changes else "COMMENT"
        body = f"**AI Code Review - {verdict}**\n\n{summary}"
        if self.dry_run:
            print(f"[dry-run] post review on PR #{pr_number} as {event}:\n{summary[:300]}...")
            return
        pr = self._repo.get_pull(pr_number)
        try:
            # COMMENT event is allowed on your own PR; REQUEST_CHANGES/APPROVE are not.
            pr.create_review(body=body, event="COMMENT")
        except Exception:
            # ultimate fallback: a plain issue comment on the PR thread
            pr.create_issue_comment(body)

    # ---- HITL approval: merge & check in -----------------------------------
    def merge_pr(self, pr_number: int | None, method: str = "squash") -> str:
        """Merge the PR once a human has approved. Returns the merge commit sha."""
        if self.dry_run:
            print(f"[dry-run] merge PR #{pr_number} via {method}")
            return "(dry-run)-merge-sha"
        pr = self._repo.get_pull(pr_number)
        res = pr.merge(merge_method=method, commit_message="Merged after human approval (HITL).")
        return res.sha


# --------------------------------------------------------------------------
# Helper: turn the Code agent's markdown into a real source file under src/.
# Extracts the first fenced code block; names it from the line-1 filename
# comment if present, else from a fallback slug. Always sanitised into src/.
# --------------------------------------------------------------------------
import re as _re

def extract_code_file(code_markdown: str, fallback_name: str = "feature") -> tuple[str, str]:
    """Return (repo_path, code_text). repo_path is always 'src/<safe>.py'."""
    m = _re.search(r"```(?:python|py)?\s*\n(.*?)```", code_markdown, _re.S)
    code = (m.group(1) if m else code_markdown).strip("\n") + "\n"

    # 1) filename from a line-1 comment like  "# reconciler_tolerance.py"
    name = None
    first = code.splitlines()[0] if code.splitlines() else ""
    fm = _re.match(r"\s*#\s*([A-Za-z0-9_\-/]+\.py)\s*$", first)
    if fm:
        name = fm.group(1).split("/")[-1]           # strip any path
    # 2) else derive from the fallback (epic/story title)
    if not name:
        slug = _re.sub(r"[^a-z0-9]+", "_", fallback_name.lower()).strip("_") or "feature"
        name = f"{slug}.py"

    # sanitise: lowercase, underscores, must end in .py, no path tricks
    name = _re.sub(r"[^a-z0-9_.\-]", "", name.lower())
    if not name.endswith(".py"):
        name += ".py"
    return f"src/{name}", code


# --------------------------------------------------------------------------
# Self-check / rehearsal: `python integrations/github_client.py`
# Runs in dry-run so it needs no token — proves the PR -> review -> merge path.
# --------------------------------------------------------------------------
if __name__ == "__main__":
    os.environ.setdefault("GITHUB_DRY_RUN", "1")
    gh = GitHubClient(dry_run=True)
    print("== Code phase -> open PR ==")
    pr = gh.open_pr(
        branch="feature/per-currency-tolerance",
        title="Add per-currency break tolerance + manager alert",
        body="Implements the reconciliation feature from the approved backlog.\n\nCloses the Jira story.",
        files={"app/reconciler.py": "# ...updated reconciler with per-currency tolerance...\n"},
    )
    print("PR:", pr.url, "\n")

    print("== PR Review phase -> post findings, request changes ==")
    gh.post_findings(
        pr.number,
        summary=("AI review + SAST findings:\n"
                 "- 🔴 Missing append-only audit of tolerance changes (control required by Risk).\n"
                 "- 🟠 Boundary bug: break equal to tolerance marked immaterial.\n"
                 "Gate decision: REQUEST CHANGES."),
        request_changes=True,
    )
    print()

    print("== HITL approves -> merge & check in ==")
    sha = gh.merge_pr(pr.number)
    print("merged sha:", sha)

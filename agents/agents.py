"""
agents.py - the six SDLC agents. Each: input -> run() -> markdown artifact.
Same uniform contract, so the orchestrator can chain them and attach tool
integrations after each human gate without changing the flow.
"""
from dataclasses import dataclass
import llm

@dataclass
class Agent:
    key: str
    title: str
    phase: str
    gate: str
    system: str
    instruction: str
    def run(self, context: str, temperature: float = 0.2) -> str:
        return llm.complete(self.system, f"{self.instruction}\n\n---\nINPUT:\n{context}", temperature)

INTAKE = Agent("intake", "Requirement Intake", "Requirements",
    "PO confirms the brief captures what the meeting decided",
    "You are a business analyst agent for a fund-administration team. You read raw Teams/Zoom "
    "transcripts full of chit-chat and distill the ACTUAL requirement. Ignore small talk. Never "
    "invent requirements. Preserve compliance/audit constraints exactly. Output clean markdown.",
    "From the transcript below produce a brief with sections: ## Feature, ## Context & problem, "
    "## Requirements discussed, ## Decisions & constraints, ## Out of scope, ## Open questions.")

PO = Agent("po", "PO / Backlog", "Requirements & Backlog",
    "PO reviews epics & stories before they enter the backlog",
    "You are a Product Owner agent. Turn a brief into a crisp, testable backlog. Concise; no preamble.",
    "From the brief below produce: ## Epic (one line), ## User Stories (2-4, 'As a.. I want.. so that..'), "
    "## Acceptance Criteria (3-5 testable), ## Out of Scope.\n"
    "Then, at the very end, output a ## Backlog JSON section containing a single fenced ```json code block "
    'with this exact shape: {"epic": "<one-line epic title>", "stories": ["<short 3-6 word story title>", ...]}. '
    "Story titles here must be SHORT labels (e.g. 'Configure Tolerances'), NOT the full 'As a...' sentence.")

CODE = Agent("code", "Code Assistant", "Software Engineering",
    "Developer reviews the code locally before commit",
    "You are a senior engineer agent. Implement the primary story cleanly and minimally. No scope creep.",
    "Implement the primary story from the backlog below. Output: ## Design note (2-3 lines), "
    "## Implementation (one fenced code block, filename comment on line 1), ## Self-review (2-3 bullets).")

TEST = Agent("test", "Test-Case (BDD)", "Quality Engineering",
    "QA refines and approves the scenarios",
    "You are a QA engineer agent working in BDD. Produce runnable, business-readable tests; flag gaps.",
    "Given the BACKLOG and IMPLEMENTATION below produce: ## Feature file (one fenced gherkin block, "
    "one Scenario per AC tagged @AC1..), ## Step definitions (one fenced code block), ## Coverage gaps.")

PR = Agent("pr", "PR Review", "Build & CI",
    "Reviewer approves before merge",
    "You are a strict-but-fair PR reviewer agent. Judge code + tests vs the acceptance criteria; focus on "
    "correctness, security, and coverage. End with a clear gate decision. No preamble.",
    "Given the BACKLOG, IMPLEMENTATION and TESTS below output: ## AC coverage (table: AC # | Implemented? | "
    "Tested? | Notes), ## Findings (grouped blocker / should-fix / nit), ## Gate decision "
    "(APPROVE MERGE or REQUEST CHANGES with one-line justification).")

RELEASE = Agent("release", "Release", "Release & Change",
    "Release manager runs the controlled release",
    "You are a release manager agent. If the review did not approve, produce a readiness assessment, not a "
    "pretend release. Output clean markdown, no preamble.",
    "Given the BACKLOG and PR REVIEW below output: ## Release readiness (GO or NO-GO, one line), "
    "## Release notes (What's new / Fixes / Known limitations), ## Deploy checklist, ## Rollback.")

REGISTRY = {a.key: a for a in [INTAKE, PO, CODE, TEST, PR, RELEASE]}
ORDER = ["intake", "po", "code", "test", "pr", "release"]

"""
app.py - Streamlit UI over the SAME 6-phase pipeline (agents + integrations).
Terminal pipeline.py stays the source of truth; this is the visual surface.

Run:  streamlit run app.py
"""
import os, sys, re, json, time
import streamlit as st
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "agents"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "integrations"))
import agents, llm

INPUTS = {"intake": [], "po": ["intake"], "code": ["po"],
          "test": ["po", "code"], "pr": ["po", "code", "test"], "release": ["po", "pr"]}
CAP = {"pr": 14000, "release": 9000, "test": 14000}
DONE, ACTIVE, TODO = "✅", "🔵", "⚪"

st.set_page_config(page_title="AI-SDLC · HITL", page_icon="🤖", layout="wide")

# ---------- styling: clean corporate (navy + teal), boardroom-ready ----------
st.markdown("""
<style>
:root { --navy:#1A2B4A; --teal:#1F6F8B; --ink:#243447; --line:#E3E8EE; }
.stApp { background:#F7F9FB; }
.hdr { background:linear-gradient(100deg,#1A2B4A 0%,#1F6F8B 100%); color:#fff;
       padding:22px 28px; border-radius:14px; margin-bottom:6px;
       box-shadow:0 6px 20px rgba(26,43,74,.15); }
.hdr h1 { color:#fff; font-size:26px; font-weight:700; margin:0; }
.hdr p  { color:#CFE0E8; font-size:14px; margin:6px 0 0; }
[data-testid="stExpander"] { border:1px solid var(--line)!important; border-radius:12px!important;
       background:#fff!important; box-shadow:0 2px 8px rgba(26,43,74,.05); margin-bottom:10px; }
[data-testid="stExpander"] summary { font-size:15.5px!important; font-weight:600!important; color:var(--navy)!important; }
[data-testid="stSidebar"] { background:#FFFFFF; border-right:1px solid var(--line); }
[data-testid="stSidebar"] h3 { color:var(--navy); }
.stButton>button { border-radius:9px; font-weight:600; border:1px solid var(--line); transition:all .15s ease; }
.stButton>button:hover { transform:translateY(-1px); box-shadow:0 3px 10px rgba(31,111,139,.2); }
.stButton>button[kind="primary"] { background:var(--teal); border-color:var(--teal); }
.badge { display:inline-block; padding:2px 10px; border-radius:20px; font-size:12px; font-weight:700; }
.b-done { background:#E4F5EC; color:#137a43; }
.b-active { background:#E8F1F6; color:#1F6F8B; }
.b-todo { background:#EEF1F4; color:#8A97A6; }
.phase-row { padding:7px 0; font-size:14px; color:var(--ink); border-bottom:1px solid #F0F3F6; }
.link-card { background:#F2F8FA; border:1px solid #D6E7EE; border-radius:9px; padding:10px 12px; margin:6px 0; }
</style>
""", unsafe_allow_html=True)
ss = st.session_state
ss.setdefault("step", 0); ss.setdefault("artifacts", {}); ss.setdefault("draft", {})
ss.setdefault("links", {}); ss.setdefault("transcript", "")
if not ss.transcript and os.path.exists("demo/transcript.txt"):
    ss.transcript = open("demo/transcript.txt", encoding="utf-8").read()

def build_ctx(key):
    parts = []
    if key == "intake": parts.append(ss.transcript)
    for p in INPUTS.get(key, []):
        if p in ss.artifacts: parts.append(ss.artifacts[p])
    ctx = "\n\n".join(x for x in parts if x).strip()
    cap = CAP.get(key)
    return ctx[:cap] + "\n\n...[trimmed]..." if cap and len(ctx) > cap else ctx

def parse_po_json(md):
    m = re.search(r"```json\s*(\{.*?\})\s*```", md, re.S)
    if not m: return None
    try:
        d = json.loads(m.group(1)); e=(d.get("epic") or "").strip()
        s=[x.strip() for x in d.get("stories",[]) if x.strip()]
        return (e, s) if e and s else None
    except Exception: return None

# ---- integration hooks (fire after a phase is approved) ----
def hook_po():
    from jira_client import JiraClient, BacklogItem
    p = parse_po_json(ss.artifacts["po"]) or ("Backlog epic", ["Primary story"])
    try:
        r = JiraClient().create_epic_and_stories(BacklogItem(epic=p[0], stories=p[1]))
        if r.url: ss.links["jira"] = r.url
        return f"Jira: {r.epic_key} + {len(r.story_keys)} stories" + (" (dry-run)" if r.dry_run else "")
    except Exception as e: return f"Jira skipped: {e}"

def hook_code():
    from github_client import GitHubClient, extract_code_file
    try:
        gh = GitHubClient()
        epic = (parse_po_json(ss.artifacts.get('po','')) or ['feature'])[0]
        code_path, code_text = extract_code_file(ss.artifacts["code"], fallback_name=epic)
        doc_path = f"docs/{code_path.split('/')[-1].replace('.py','')}_change.md"
        pr = gh.open_pr(branch=f"feature/ui-{time.strftime('%Y%m%d-%H%M%S')}",
                        title=epic if epic != 'feature' else 'Implement backlog item',
                        body=f"Implements the approved backlog (via Streamlit UI).\n\nAdds `{code_path}` with design note & self-review in `docs/`.",
                        files={code_path: code_text, doc_path: ss.artifacts["code"]})
        ss.links["pr"] = pr.url; ss.setdefault("pr_number", pr.number)
        ss["code_path"] = code_path
        return f"PR opened: {pr.url}  ·  committed {code_path}" + (" (dry-run)" if pr.dry_run else "")
    except Exception as e: return f"PR skipped: {e}"

def hook_pr():
    from github_client import GitHubClient
    review = ss.artifacts["pr"]; rc = "REQUEST CHANGES" in review.upper()
    try:
        GitHubClient().post_findings(ss.get("pr_number"), summary=review, request_changes=rc)
        return f"Review posted ({'REQUEST_CHANGES' if rc else 'COMMENT'})"
    except Exception as e: return f"Review skipped: {e}"

HOOKS = {"po": hook_po, "code": hook_code, "pr": hook_pr}

# ---- sidebar ----
with st.sidebar:
    st.markdown("### AI-SDLC · HITL")
    st.caption("Transcript → release. Every phase automated, human-approved at each gate.")
    st.markdown(f"**Model:** `{llm.provider()}:{llm.model()}`")
    st.divider(); st.markdown("**Pipeline**")
    for i, k in enumerate(agents.ORDER):
        a = agents.REGISTRY[k]
        if k in ss.artifacts:
            cls, lbl = "b-done", "done"
        elif i == ss.step:
            cls, lbl = "b-active", "active"
        else:
            cls, lbl = "b-todo", "pending"
        st.markdown(
            f"<div class='phase-row'>{i+1}. {a.title} "
            f"<span class='badge {cls}' style='float:right'>{lbl}</span></div>",
            unsafe_allow_html=True)
    if ss.links:
        st.divider(); st.markdown("**Live artifacts**")
        if "jira" in ss.links:
            st.markdown(f"<div class='link-card'>🎫 <a href='{ss.links['jira']}' target='_blank'>Jira epic</a></div>", unsafe_allow_html=True)
        if "pr" in ss.links:
            st.markdown(f"<div class='link-card'>🔀 <a href='{ss.links['pr']}' target='_blank'>GitHub PR</a></div>", unsafe_allow_html=True)
    st.divider()
    if st.button("↺ Reset run", use_container_width=True):
        for k in ("step","artifacts","draft","links","pr_number"): ss.pop(k, None)
        st.rerun()

st.markdown(
    "<div class='hdr'><h1>AI-Powered SDLC — Human-in-the-Loop</h1>"
    "<p>One meeting transcript, six agents, a human gate at every phase — "
    "with real Jira, GitHub PRs and AI code review.</p></div>",
    unsafe_allow_html=True)
st.write("")

with st.expander("① Requirement source — meeting transcript (Teams / Zoom)", expanded=(ss.step == 0)):
    up = st.file_uploader("Upload transcript (.txt/.vtt/.md)", type=["txt","vtt","md"])
    if up is not None: ss.transcript = up.read().decode("utf-8", errors="ignore")
    ss.transcript = st.text_area("…or paste / edit here", ss.transcript, height=160)

st.divider()

for i, key in enumerate(agents.ORDER):
    a = agents.REGISTRY[key]; done = key in ss.artifacts
    active = (i == ss.step) and not done; locked = i > ss.step and not done
    head = f"{DONE if done else (ACTIVE if active else TODO)} **{i+1}. {a.title}** — _{a.phase}_"
    with st.expander(head, expanded=active):
        st.caption(f"🔒 Human gate: {a.gate}")
        if locked:
            st.write("Approve the previous phase to unlock."); continue
        if key not in ss.draft and not done:
            if key == "intake" and not ss.transcript.strip():
                st.warning("Add a transcript above first.")
            elif st.button(f"▶ Run {a.title}", key=f"run_{key}", type="primary"):
                with st.spinner(f"{a.title} working… ({llm.provider()}) — a few seconds"):
                    try: ss.draft[key] = a.run(build_ctx(key))
                    except BaseException as e: st.error(f"Agent failed — {type(e).__name__}: {e}")
        if key in ss.draft and not done:
            st.success("Draft ready — review, edit if needed, then approve.")
            edited = st.text_area("Agent output (editable)", ss.draft[key], height=320, key=f"ed_{key}")
            c1, c2, _ = st.columns([1.2, 1, 4])
            if c1.button("✅ Approve & continue", key=f"ok_{key}", type="primary"):
                ss.artifacts[key] = edited; ss.draft.pop(key, None)
                ss.step = min(i+1, len(agents.ORDER))
                try: open(f"{i:02d}_{key}.md","w",encoding="utf-8").write(edited)
                except Exception: pass
                if key in HOOKS:
                    with st.spinner(f"Firing {key} integration…"):
                        ss.setdefault("hook_msg", {})[key] = HOOKS[key]()
                st.rerun()
            if c2.button("🔁 Regenerate", key=f"rg_{key}"):
                ss.draft.pop(key, None); st.rerun()
        if done:
            if ss.get("hook_msg", {}).get(key): st.info("→ " + ss["hook_msg"][key])
            st.markdown(ss.artifacts[key])

if len(ss.artifacts) == len(agents.ORDER):
    st.success("🎉 Full SDLC cycle complete — every phase automated, every gate human-approved.")
    if ss.links.get("jira"): st.markdown(f"🎫 **Jira epic:** {ss.links['jira']}")
    if ss.links.get("pr"): st.markdown(f"🔀 **GitHub PR:** {ss.links['pr']}")

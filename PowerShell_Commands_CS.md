# AI-SDLC-HITL — PowerShell Command Cheat Sheet

Everything we ran, grouped by task. Project folder:
`C:\Users\omkshukl\Code\ai-sdlc-hitl`

> Tip: always check your prompt shows **(.venv)** and the VS Code bottom-left shows the
> right **branch** before running.

---

## 0. Every-time setup (open a fresh terminal)

```powershell
cd C:\Users\omkshukl\Code\ai-sdlc-hitl     # go to the project
.\.venv\Scripts\Activate.ps1               # activate the virtual env -> prompt shows (.venv)
```

If activation is blocked by execution policy:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

---

## 1. Virtual environment (.venv)

```powershell
# does this folder have a venv?
Test-Path .\.venv

# create one (only if missing)
python -m venv .venv

# activate it
.\.venv\Scripts\Activate.ps1

# confirm which python you're using (should end in ...\ai-sdlc-hitl\.venv\Scripts\python.exe)
python -c "import sys; print(sys.executable)"

# find every .venv on the machine (if you lose track)
Get-ChildItem -Path C:\Users\omkshukl -Recurse -Directory -Filter ".venv" -ErrorAction SilentlyContinue | Select-Object FullName
```

---

## 2. Install dependencies

```powershell
# the reliable form (always installs into the python you're running)
python -m pip install -r requirements.txt

# individual packages if ever needed
python -m pip install pytest
python -m pip install streamlit
python -m pip install openai python-dotenv PyGithub
```

---

## 3. Run the pipeline (terminal demo — the reliable one)

```powershell
# interactive — you approve each gate (a / r / q), real integrations fire
python pipeline.py --in demo/transcript.txt

# auto mode — no prompts, approves everything (quick smoke test)
python pipeline.py --in demo/transcript.txt --auto
```

At each gate you type:
- `a` = approve & continue
- `r` = regenerate that phase
- `q` = quit

---

## 4. Run the Streamlit UI (the visual demo)

```powershell
python -m streamlit run app.py
```
Opens http://localhost:8501 . Stop it with **Ctrl+C** in the terminal.

---

## 5. Run the tests

```powershell
python -m pytest tests/ -q
```

---

## 6. Switching to mock / dry-run (rehearse safely, no real tickets/PRs, no network)

Set these in your **.env** file (open it with `code .env`):
```
LLM_PROVIDER=mock       # canned output, no Groq call
JIRA_DRY_RUN=1          # simulate Jira, no real ticket
GITHUB_DRY_RUN=1        # simulate PR, no real PR
```
Remove/blank those lines (or set DRY_RUN to 0) to go LIVE again.

Quick one-off mock run without editing .env:
```powershell
$env:LLM_PROVIDER="mock"; python pipeline.py --in demo/transcript.txt --auto
# clear it again for this terminal:
$env:LLM_PROVIDER=""
```

---

## 7. The .env file (secrets — never commit this)

```powershell
# create it from the template
Copy-Item .env.example .env

# open it to edit
code .env

# view what's in it (careful — shows secrets)
Get-Content .env
```

Contents (fill in your real values):
```
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_...
GITHUB_TOKEN=ghp_...
GITHUB_REPO=Thecodewulf/ai-sdlc-hitl
JIRA_SITE=https://oshuklaiim.atlassian.net
JIRA_EMAIL=oshukla.iim@gmail.com
JIRA_TOKEN=...
JIRA_PROJECT=AIS
```

---

## 8. Git — everyday flow

```powershell
git status                      # what's changed / which branch
git add .                       # stage everything
git commit -m "your message"    # commit (note: -m is required)
git push                        # push to GitHub
```

Safety check before committing (make sure secrets aren't staged):
```powershell
git status                      # .env should NOT be listed; only .env.example
```

---

## 9. Git — branches (main = terminal demo, streamlit-ui = the UI)

```powershell
git branch                      # list branches; * marks current

git checkout main               # switch to main
git checkout streamlit-ui       # switch to the UI branch
git checkout -b new-branch      # create AND switch to a new branch

git push -u origin streamlit-ui # first push of a new branch (links it)
```

---

## 10. Git — "push rejected / fetch first" (remote is ahead)

Happens after PRs get merged on GitHub. Fix:
```powershell
git pull origin main --no-edit
git push
```
If it drops you into the Vim editor, type `:wq` then Enter.

---

## 11. First-time repo setup (reference — already done)

```powershell
git init
git branch -M main
git config user.name "Omkar Shukla"
git config user.email "oshukla.iim@gmail.com"
git remote add origin https://github.com/Thecodewulf/ai-sdlc-hitl.git
git add .
git commit -m "Initial scaffold"
git push -u origin main
```

Clone instead (if starting on a new machine):
```powershell
cd C:\Users\omkshukl\Code
git clone https://github.com/Thecodewulf/ai-sdlc-hitl.git
cd ai-sdlc-hitl
```

---

## 12. Handy checks / troubleshooting

```powershell
git --version                   # is git installed?
python --version                # python version
Test-Path pipeline.py           # is a file here?
dir                             # list files in this folder
dir demo                        # list the demo folder (transcript.txt lives here)

# clear stale compiled python if a fix "won't take"
Remove-Item -Recurse -Force integrations\__pycache__ -ErrorAction SilentlyContinue

# see a file's contents
Get-Content pipeline.py

# check a specific line pattern in a file
Select-String -Path integrations\github_client.py -Pattern "event="

# test Groq connectivity (network / key sanity)
curl https://api.groq.com/openai/v1/models -H "Authorization: Bearer $env:GROQ_API_KEY"
```

---

## 13. The two known live-demo gotchas (and the quick fix)

- **Groq rate limit (413 / 429 "tokens per minute"):** free tier is 8000 TPM.
  Fix: wait ~60 seconds and re-run, OR use mock mode (section 6), OR upgrade Groq Dev tier.

- **Network / SSL handshake timeout:** usually corporate VPN/wifi.
  Fix: retry once; if it persists, switch to phone hotspot. Test the demo-room network beforehand.

---

## Quick "just run the demo" sequence

```powershell
cd C:\Users\omkshukl\Code\ai-sdlc-hitl
.\.venv\Scripts\Activate.ps1
python pipeline.py --in demo/transcript.txt         # terminal demo
# — or —
python -m streamlit run app.py                      # UI demo
```

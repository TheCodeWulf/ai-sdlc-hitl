"""llm.py - provider-agnostic client. Groq by default; 'mock' needs no key."""
import os
try:
    from dotenv import load_dotenv; load_dotenv()
except Exception:
    pass

PROVIDERS = {
    "groq":   {"base_url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b", "key_env": "GROQ_API_KEY"},
    "ollama": {"base_url": "http://localhost:11434/v1", "model": "llama3.1", "key_env": None},
    "mock":   {"base_url": None, "model": "mock", "key_env": None},
}

def provider(): return os.getenv("LLM_PROVIDER", "groq").strip().lower()
def model():     return os.getenv("LLM_MODEL") or PROVIDERS.get(provider(), PROVIDERS["mock"])["model"]

def complete(system: str, user: str, temperature: float = 0.2) -> str:
    p = provider(); cfg = PROVIDERS.get(p, PROVIDERS["mock"])
    if p == "mock":
        return _mock(system, user)
    from openai import OpenAI
    key = os.getenv(cfg["key_env"]) if cfg["key_env"] else "ollama"
    if cfg["key_env"] and not key:
        raise SystemExit(f"Missing {cfg['key_env']} in .env (or use LLM_PROVIDER=mock).")
    client = OpenAI(base_url=cfg["base_url"], api_key=key or "none")
    r = client.chat.completions.create(model=model(),
        messages=[{"role":"system","content":system},{"role":"user","content":user}],
        temperature=temperature)
    return r.choices[0].message.content.strip()

def _mock(system: str, user: str) -> str:
    tag = system.split(".")[0][:48]
    return f"[MOCK - {tag}]\n(Set LLM_PROVIDER=groq + GROQ_API_KEY for real output.)\n"

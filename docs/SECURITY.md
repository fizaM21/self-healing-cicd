# Security notes

## Threat model
CI logs and repo code are **untrusted**. The LLM can be tricked by text in logs (prompt injection). Therefore the LLM is never trusted: every patch passes a policy check, a human approves it, and it runs only in a sandbox.

## Controls
| Risk | Control |
|---|---|
| Fake webhooks | HMAC-SHA256 signature, constant-time compare. No secret configured = reject all |
| Unauthorized API use | `X-API-Key`, constant-time compare |
| Secrets in logs reaching LLM / DB / UI | Regex redaction (GitHub, Groq, Google, AWS keys, private keys, `password=`, auth headers) |
| Malicious patch (edit CI, steal secrets) | Policy: max 5 edits, no `..`/absolute paths, `.github/`, `.git/`, `.env*` blocked; checked again at apply time |
| Bad code run by tests | Sandbox: scrubbed env (no tokens). Docker mode: 1 CPU, 1 GB, 256 PIDs, all capabilities dropped, no-new-privileges, timeout |
| Token leak | Git auth by HTTP header (not in URL or `.git/config`); Gemini key in header |
| Shell injection via branch names | Notify workflow passes values as env vars, never inline |
| Infinite loops | Workflows named `Self-Heal*` are ignored; one incident per (repo, run) |
| Dashboard exposure | Basic auth (fails closed), server-side proxy with path allowlist, API key never sent to browser |

## Important limits
- **`SANDBOX_MODE=local` is not isolation.** Tests run as the backend user. Use only for development, or only on repos you fully trust (it is the default on Render because Render has no Docker).
- **Docker mode** with `docker.sock` gives the backend control of the host. Use a dedicated VM.
- Test code can still reach the network in Docker mode (needed for `pip`/`npm`). Add egress rules for stricter setups.
- Basic auth is a minimal gate. For teams, put the dashboard behind SSO (e.g. Vercel Authentication, Cloudflare Access).
- Rotate any token that was ever pasted in a chat, issue, or log.

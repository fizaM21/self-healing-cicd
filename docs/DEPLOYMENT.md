# Deployment

## Backend → Render (Docker + PostgreSQL)
1. Render dashboard → **New → Blueprint** → pick this repo. `render.yaml` creates the web service and a database.
2. Set the `sync: false` variables: `GROQ_API_KEY`, `GITHUB_TOKEN`, `CORS_ORIGINS` (your Vercel URL).
3. Copy the generated `API_KEY` and `GITHUB_WEBHOOK_SECRET` (service → Environment). You need them below.
4. Optional auto deploy after CI: service → Settings → Deploy Hook → add it as GitHub secret `RENDER_DEPLOY_HOOK_URL`.
5. Verify: `curl https://<service>.onrender.com/health` → `{"status":"ok"}`.

## Frontend → Vercel
1. Import the repo, set **Root Directory** to `frontend`.
2. Environment variables: `BACKEND_URL` (Render URL), `BACKEND_API_KEY` (= backend `API_KEY`), `DASHBOARD_USER`, `DASHBOARD_PASSWORD`.
3. Deploy and open the URL. A Basic-auth prompt must appear.

## Connect repos
See "Connect a repository" in the README.

## Production checklist
- [ ] Strong random `API_KEY`, webhook secret, dashboard password
- [ ] Fine-grained `GITHUB_TOKEN` limited to the repos you protect
- [ ] Decide sandbox: dedicated Docker VM (best) or trusted repos only (`local`)
- [ ] Add Alembic migrations before schema changes
- [ ] Scrape `/metrics` (needs `X-API-Key`) with Prometheus / Grafana

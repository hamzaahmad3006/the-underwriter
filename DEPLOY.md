# Deploying The Underwriter

Two services: one backend container on Fly.io with a persistent volume, one
static frontend on Vercel. OPS-025 wants the public URL reachable through
submission.

**The one constraint that shapes everything here:** exactly one backend
instance may run (OPS-020). The scheduler is in-process, so a second machine is
a second scheduler double-submitting every cycle. `fly.toml` already pins
`max_machines_running = 1`, disables autoscaling, and deploys `immediate`
rather than rolling — a rolling deploy would briefly run two.

---

## 0. Verify the image builds

Not yet done on this machine — Docker Desktop was not running — so do this
first rather than discovering it mid-deploy.

```bash
cd backend
docker build -t the-underwriter:local .
docker run --rm -p 8000:8000 --env-file .env the-underwriter:local
curl http://127.0.0.1:8000/health
```

Expect `{"status":"ok",...}`. If the build fails, the likely cause is the
`pip install .` layer: it installs from `pyproject.toml`, so a dependency that
only exists in the local venv will not be there.

---

## 1. Backend on Fly.io

```bash
cd backend
fly auth login
fly launch --no-deploy --copy-config --name the-underwriter --region iad
```

`--copy-config` keeps the committed `fly.toml`. If Fly offers to add a
Postgres or Redis, decline both — TD-02 is SQLite on a volume, deliberately.

### The volume

NFR-005 wants committed records to survive a restart, which needs the database
to outlive the container.

```bash
fly volumes create underwriter_data --size 1 --region iad
```

### Secrets

Never in the image, never in git (OPS: platform secret store).

```bash
fly secrets set \
  ALPACA_API_KEY="..." \
  ALPACA_SECRET_KEY="..." \
  GROQ_API_KEY="..." \
  KERNEL_SIGNING_SECRET="..." \
  OPERATOR_TOKEN="..." \
  ALPACA_PAPER_TRADE="true" \
  GROQ_MODEL="openai/gpt-oss-120b" \
  GROQ_MODEL_FALLBACK="openai/gpt-oss-20b" \
  CORS_ORIGINS="https://<your-vercel-domain>"
```

Copy `KERNEL_SIGNING_SECRET` and `OPERATOR_TOKEN` from your local `.env` — they
were generated there. **Do not** set `GROQ_BASE_URL`: the SDK appends
`/openai/v1` itself, and an empty or doubled value fails every call (ASM-006).

### Deploy and check

```bash
fly deploy
fly logs
curl https://the-underwriter.fly.dev/health
curl https://the-underwriter.fly.dev/api/health/deep
```

Readiness returns 200 when nothing is `down`. `alpaca_rest: degraded` is
expected and permanent — ALP-004 issues no separate data key on this plan.

Confirm there is exactly one machine:

```bash
fly status          # one machine, started
fly scale count 1   # only if it shows more
```

---

## 2. Frontend on Vercel

Point `vercel.json`'s rewrite at your actual Fly hostname first:

```jsonc
{ "source": "/api/:path*", "destination": "https://the-underwriter.fly.dev/api/:path*" }
```

```bash
cd frontend
vercel                # preview
vercel --prod         # production
```

Then set `CORS_ORIGINS` on Fly to the production domain and redeploy the
backend, or the browser will be refused (SEC-007 forbids a wildcard).

---

## 3. After the first deploy

The desk boots in `MANAGE_ONLY` every time (ERR-007). That is deliberate: after
a restart the book and the broker may disagree, and the first cycle must not
open a position on top of a divergence nobody has looked at.

```bash
# Watch the reconcile cycle land (it runs every 5 minutes)
curl https://the-underwriter.fly.dev/api/scheduler/runs | jq '.runs[0]'

# Confirm the account baseline was recorded
curl https://the-underwriter.fly.dev/api/dashboard/overview | jq '.capital'

# Then promote to ACTIVE so entries can be written
curl -X POST https://the-underwriter.fly.dev/api/system/mode \
  -H "Authorization: Bearer $OPERATOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"ACTIVE"}'
```

Promoting is an operator action on purpose — someone should have looked at the
book after the process came back.

### The kill switch

```bash
curl -X POST https://the-underwriter.fly.dev/api/system/kill-switch \
  -H "Authorization: Bearer $OPERATOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"engaged":true}'
```

Takes effect within one scheduler cycle: SK-023 reads it on every adjudication.

---

## 4. Rollback

The volume is untouched by a deploy, so a rollback is one command:

```bash
fly releases            # find the previous version
fly deploy --image <previous image digest>
```

OPS-022: avoid deploying during market hours except for a critical fix. A
deploy mid-cycle is safe — idempotent `client_order_id` and boot reconciliation
handle it — but "safe" and "wise" are different words.

---

## Demo checklist

Three things worth showing, in this order:

1. **The Kernel refuses.** Dashboard → Kernel → *Catastrophic proposal*. Eight
   rules fail at once, `executed: false`, and there is no override control on
   the page because there is no override in the system.
2. **The operator is not privileged.** `POST /api/policies/{id}/close` on a
   halted desk returns 403 with the failing rules. The same pipeline, the same
   rules, for a human.
3. **Determinism is checkable.** `GET /api/underwriting/replay/{decision_id}`
   returns an empty diff. Edit one stored quote and it returns
   `deterministic: false` and raises a CRITICAL risk event.

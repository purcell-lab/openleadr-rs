# Deploying OpenLEADR-rs VTN to Fly.io (Sydney) via GitHub Actions

This bundle contains:

- `fly.toml` — Fly app config (Sydney region, scale-to-zero, 512 MB VM)
- `.github/workflows/fly-deploy.yml` — auto-deploy on push to `main`

The upstream `vtn.Dockerfile` and `docker-compose.yml` already exist in
`OpenLEADR/openleadr-rs`, so we only need these two files plus a Fly app
and a managed Postgres cluster.

---

## 1. Fork upstream into your GitHub account

You'll need a fork because the Actions workflow has to live in a repo you
control (so you can add the `FLY_API_TOKEN` secret).

```bash
# On github.com: Fork OpenLEADR/openleadr-rs → mark-purcell/openleadr-rs
git clone git@github.com:<your-username>/openleadr-rs.git
cd openleadr-rs
```

Copy `fly.toml` to the repo root and the workflow to `.github/workflows/`:

```bash
cp /path/to/fly.toml ./fly.toml
mkdir -p .github/workflows
cp /path/to/fly-deploy.yml .github/workflows/fly-deploy.yml
```

## 2. Install flyctl and sign in (one-time, on your laptop)

```bash
brew install flyctl           # macOS
fly auth signup               # or: fly auth login
```

Add a payment method at https://fly.io/dashboard/<org>/billing — Fly
removed the always-free tier in late 2024; expect ~USD $3–8/mo for a
small VTN + tiny Postgres that scales to zero when idle.

## 3. Create the Fly app

From the repo root (don't run `fly launch` — we already have `fly.toml`):

```bash
# Pick a unique app name — it becomes <name>.fly.dev
fly apps create mark-vtn --org personal
```

Edit `fly.toml` and replace `<YOUR-APP-NAME>` with `mark-vtn`.

## 4. Create Managed Postgres (Sydney)

Fly's newer Managed Postgres service (MPG) is the recommended option:

```bash
# Creates a managed cluster and prints connection details
fly managed-postgres create \
    --name mark-vtn-db \
    --region syd \
    --plan basic
```

When it finishes, grab the connection string:

```bash
fly managed-postgres connection-string mark-vtn-db
# postgres://<user>:<password>@<host>:5432/<db>?sslmode=require
```

> If `managed-postgres` isn't enabled on your org yet, fall back to the
> older unmanaged Postgres with `fly postgres create --region syd --name mark-vtn-db --vm-size shared-cpu-1x --volume-size 1`
> and `fly postgres attach --app mark-vtn mark-vtn-db`.

## 5. Set app secrets

The VTN reads its config from env vars. Set them as Fly secrets so they're
encrypted at rest and available to the running VM:

```bash
APP=mark-vtn
URL="https://${APP}.fly.dev"

# Generate a random base64 secret for signing OAuth tokens
OAUTH_SECRET=$(openssl rand -base64 48)

fly secrets set --app "$APP" \
  DATABASE_URL="postgres://USER:PASS@HOST:5432/DB?sslmode=require" \
  OAUTH_TOKEN_URL="${URL}/auth/token" \
  OAUTH_VALID_AUDIENCES="${URL}" \
  OAUTH_BASE64_SECRET="${OAUTH_SECRET}"
```

> Keep `OAUTH_BASE64_SECRET` somewhere safe (1Password) — rotating it
> invalidates every issued access token.

## 6. Wire up GitHub Actions

Create a deploy token scoped to this one app:

```bash
fly tokens create deploy --app mark-vtn --expiry 8760h
# copy the printed token
```

In GitHub: **Settings → Secrets and variables → Actions → New repository
secret** → name `FLY_API_TOKEN`, value = the token you just generated.

Commit and push:

```bash
git add fly.toml .github/workflows/fly-deploy.yml
git commit -m "Deploy VTN to Fly.io (syd) via GitHub Actions"
git push origin main
```

Watch the deploy in the **Actions** tab. First build takes ~5–8 min
(Rust release build inside Alpine); subsequent deploys reuse the cache
and are faster.

## 7. First-time database bootstrap

Migrations run automatically on VTN startup (the binary calls
`sqlx::migrate!("./migrations")`), so the schema is created on first
boot. To load the test users / OAuth clients from `fixtures/users.sql`:

```bash
# Open a psql shell against managed Postgres
fly managed-postgres connect mark-vtn-db
# then at the psql prompt:
\i fixtures/users.sql
```

Or one-shot:

```bash
psql "$(fly managed-postgres connection-string mark-vtn-db)" \
     -f fixtures/users.sql
```

That gives you two ready-to-use OAuth clients:

| client_id              | client_secret  | scopes                     |
|------------------------|----------------|----------------------------|
| `bl-client`            | `bl-client`    | Business-logic / admin     |
| `ven-client-client-id` | `ven-client`   | VEN-side (read+write reports) |

⚠️ Rotate these before pointing real devices at the VTN — they're
public test credentials.

## 8. Smoke test

```bash
curl https://mark-vtn.fly.dev/

# OAuth token request
curl -s -X POST https://mark-vtn.fly.dev/auth/token \
  -d "grant_type=client_credentials" \
  -d "client_id=bl-client" \
  -d "client_secret=bl-client"
# → {"access_token":"eyJ...","token_type":"Bearer","expires_in":3600}

TOKEN=$(curl -s -X POST https://mark-vtn.fly.dev/auth/token \
  -d "grant_type=client_credentials&client_id=bl-client&client_secret=bl-client" \
  | jq -r .access_token)

# List programs (empty array on a fresh DB)
curl -H "Authorization: Bearer $TOKEN" https://mark-vtn.fly.dev/programs
```

## 9. Day-to-day commands

| Task | Command |
|---|---|
| Tail logs | `fly logs --app mark-vtn` |
| SSH into the VM | `fly ssh console --app mark-vtn` |
| Manual deploy from laptop | `fly deploy --dockerfile vtn.Dockerfile` |
| psql into the DB | `fly managed-postgres connect mark-vtn-db` |
| Resize VM | `fly scale vm shared-cpu-2x --memory 1024` |
| See current secrets (names only) | `fly secrets list --app mark-vtn` |

## 10. Cost sanity check

With `auto_stop_machines = "stop"` and `min_machines_running = 0`:

- VM (`shared-cpu-1x`, 512 MB) when running: ~USD $1.94/mo equivalent,
  but you only pay while it's awake.
- Managed Postgres `basic`: ~USD $5/mo (smallest tier; check current
  pricing at https://fly.io/docs/about/pricing/).
- Outbound bandwidth: free for first 100 GB/mo in your region.

For a personal sandbox that wakes a few times a day, expect <USD $10/mo.

---

## Notes specific to your stack

- **CSIP-AUS / Dynamic Exports overlap** — OpenADR 3.0's `program` →
  `event` → `interval` shape maps loosely onto CSIP-AUS DERControl
  scheduling. If you want to use this VTN as a translation layer in
  front of HAEO, the `bl-client` credential is the one you'd give your
  control plane.
- **Auto-stop and OpenADR polling** — VENs that long-poll every few
  seconds will keep the machine awake, which is fine; VENs that poll
  rarely will let it sleep, saving credits.
- **Custom domain** — `fly certs create vtn.purcell.id.au --app mark-vtn`
  then point a CNAME at `mark-vtn.fly.dev`. After the cert validates,
  update `OAUTH_TOKEN_URL` / `OAUTH_VALID_AUDIENCES` secrets accordingly.

# Deploying OpenLEADR-rs VTN to Fly.io (Sydney) via GitHub Actions

This bundle contains:

- `fly.toml` — Fly app config (Sydney region, scale-to-zero, 512 MB VM)
- `.github/workflows/fly-deploy.yml` — auto-deploy on push to `main`

The upstream `vtn.Dockerfile` and `docker-compose.yml` already exist in
`OpenLEADR/openleadr-rs`, so we only need these two files plus a Fly app
and a managed Postgres cluster.

## Current deployment and upgrades

The live app is `openleadr-vtn-au` in `syd`. This branch is upstream
[v0.2.8](https://github.com/OpenLEADR/openleadr-rs/releases/tag/v0.2.8) plus
two local changes:

- this `deploy/` folder, `fly.toml` and `.github/workflows/fly-deploy.yml`;
- **public reads**: when the Fly secret `PUBLIC_READS=true` is set,
  `GET /programs`, `/programs/{id}`, `/events` and `/events/{id}` accept
  requests with no token, as a `read_all` user. Every other endpoint, and
  every write, still needs a Bearer token. This change is for this
  deployment only and must not go upstream.

### Upgrading from the May 2026 build (about v0.2.2)

1. **Back up the database first.** Five migrations run on startup and
   two of them rebuild the `scope` enum, so they cannot be rolled back:
   `resource_groups`, `subscriptions_client_id` (`write_subscriptions`
   becomes `write_subscriptions_ven`), the `rg_family` view,
   `write_reports_bl`/`write_reports_ven` (`write_reports` becomes
   `write_reports_ven`), and dropping the unique index on report names.
2. **Keep `PUBLIC_READS`.** It is a Fly secret, not in `fly.toml`.
   Check with `fly secrets list --app openleadr-vtn-au`.
3. **MQTT stays off.** v0.2.8 only enables MQTT when `MQTT_URL`,
   `MQTT_USERNAME` and `MQTT_PASSWORD` are all set. Setting only some of
   them is an error.
4. The build now needs cmake, a C++ compiler and OpenSSL headers for
   `paho-mqtt`. Upstream's `vtn.Dockerfile` installs them, so a remote
   Fly build works without changes. It takes a few minutes longer.
5. Deploy: `fly deploy --app openleadr-vtn-au --remote-only --dockerfile vtn.Dockerfile`.
6. Check: `python deploy/check_vtn.py` (read-only, no token needed while
   public reads are on). Then confirm the bridge is still publishing.

### Credentials

The business-logic client is `bl-admin-2611` on the VTN user `bl-client`.
Its secret is in Home Assistant `secrets.yaml` as
`openadr_vtn_client_secret` (see purcell-lab/ha-config #638). Never
commit it. The `fixtures/users.sql` test logins are not loaded on this
deployment.

The GitHub Actions deploy needs a repository secret `FLY_API_TOKEN`
(section 6). It is not set yet, so pushes to `main` do not deploy.

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
fly apps create openleadr-vtn-au --org personal
```

Edit `fly.toml` and replace `<YOUR-APP-NAME>` with `openleadr-vtn-au`.

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
> and `fly postgres attach --app openleadr-vtn-au mark-vtn-db`.

## 5. Set app secrets

The VTN reads its config from env vars. Set them as Fly secrets so they're
encrypted at rest and available to the running VM:

```bash
APP=openleadr-vtn-au
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
fly tokens create deploy --app openleadr-vtn-au --expiry 8760h
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
curl https://openleadr-vtn-au.fly.dev/

# OAuth token request (CLIENT_SECRET from HA secrets.yaml, never committed)
curl -s -X POST https://openleadr-vtn-au.fly.dev/auth/token \
  -d "grant_type=client_credentials" \
  -d "client_id=bl-admin-2611" \
  --data-urlencode "client_secret=$CLIENT_SECRET"
# → {"access_token":"eyJ...","token_type":"Bearer","expires_in":3600}

TOKEN=$(curl -s -X POST https://openleadr-vtn-au.fly.dev/auth/token \
  -d "grant_type=client_credentials&client_id=bl-admin-2611" --data-urlencode "client_secret=$CLIENT_SECRET" \
  | jq -r .access_token)

# List programs (empty array on a fresh DB)
curl -H "Authorization: Bearer $TOKEN" https://openleadr-vtn-au.fly.dev/programs
```

## 9. Day-to-day commands

| Task | Command |
|---|---|
| Tail logs | `fly logs --app openleadr-vtn-au` |
| SSH into the VM | `fly ssh console --app openleadr-vtn-au` |
| Manual deploy from laptop | `fly deploy --dockerfile vtn.Dockerfile` |
| psql into the DB | `fly managed-postgres connect mark-vtn-db` |
| Resize VM | `fly scale vm shared-cpu-2x --memory 1024` |
| See current secrets (names only) | `fly secrets list --app openleadr-vtn-au` |

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
- **Custom domain** — `fly certs create vtn.purcell.id.au --app openleadr-vtn-au`
  then point a CNAME at `openleadr-vtn-au.fly.dev`. After the cert validates,
  update `OAUTH_TOKEN_URL` / `OAUTH_VALID_AUDIENCES` secrets accordingly.

## MQTT notifications

The VTN publishes OpenADR notifications to a Mosquitto broker in the
separate Fly app `openleadr-mqtt-au` (Sydney). The broker files are in
`deploy/mqtt/`.

| Path | Address | Who |
|---|---|---|
| VTN to broker | `tcp://openleadr-mqtt-au.internal:1883` (Fly private network only) | user `vtn`, publishes every topic |
| Public subscribers | `wss://openleadr-mqtt-au.fly.dev:443` (Fly terminates TLS) | anonymous, read-only on `programs/#`, `events/#`, `push/programs/#`, `push/events/#` |

The `vens/`, `reports/`, `resources/` and `resource_groups/` topics are not
readable anonymously. Messages are QoS 0 and not retained, so a new
subscriber sees the next update. On each bridge cycle every event produces an
`events/...` message carrying the full event (about 78 KB for a 7-day
program) and a small `push/events/...` message.

### Deploy the broker

```bash
cd deploy/mqtt
fly apps create openleadr-mqtt-au --org personal
fly secrets set MQTT_VTN_PASSWORD=<random> -a openleadr-mqtt-au --stage
fly deploy --remote-only --ha=false -a openleadr-mqtt-au
```

### Point the VTN at the broker

```bash
fly secrets set -a openleadr-vtn-au \
  MQTT_URL=tcp://openleadr-mqtt-au.internal:1883 \
  MQTT_ADVERTISE_URL=wss://openleadr-mqtt-au.fly.dev:443 \
  MQTT_USERNAME=vtn MQTT_PASSWORD=<same random>
```

The VTN connects to the broker at start-up and refuses to start if it cannot.
The broker therefore runs with `auto_stop_machines = "off"` and
`min_machines_running = 1`. To turn MQTT off, unset all four `MQTT_*`
secrets on `openleadr-vtn-au`.

### Test as an anonymous subscriber

```python
import paho.mqtt.client as m, ssl
c = m.Client(m.CallbackAPIVersion.VERSION2, transport="websockets")
c.tls_set(cert_reqs=ssl.CERT_REQUIRED)
c.on_message = lambda c, u, msg: print(msg.topic, len(msg.payload))
c.connect("openleadr-mqtt-au.fly.dev", 443)
c.subscribe("events/#")
c.loop_forever()
```

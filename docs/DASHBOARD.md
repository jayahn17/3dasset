# Customer dashboard (Vercel)

The last mile: iOS capture → processed here → published → customer downloads
the file that matches their tool.

```
iOS CrateScanner ──► Drive ──► [GPU box] assetpipe ──► demo_out/ cad_out/ sim_out/
                                                              │
                                        tools/publish_dashboard.py
                                          ├─ classify each artifact → target app
                                          ├─ upload deliverables ─┐
                                          └─ upload manifest.json ┴─► Vercel Blob
                                                              │
                                                    web/ on Vercel
                                          fetches the manifest at request time
```

**The manifest lives in blob storage, not in the web repo.** That is the whole
design: publishing a new scan is an upload, never a redeploy. Both routes are
`force-dynamic`, so the next page view shows the new scan.

---

# Running the commands in this document

**There is no `python` on this box.** A fresh shell has no conda env active
(`~/.bashrc` initialises conda but activates nothing), so `python tools/...`
exits 127 with `python: command not found` and nothing else — including the
setup check below, whose entire job is to hand you a verdict. Every command
here therefore names the interpreter in full:

```
/home/jaeahn-jammy/miniconda3/envs/assetpipe/bin/python
```

That is the same interpreter the installed cron line uses, so a command you run
by hand and the command cron runs are the same command. If you would rather
type less, `conda activate assetpipe` (as in the top-level README) makes a bare
`python` resolve to that exact file — but nothing in this document assumes you
did.

Two more conventions, so every block can be pasted whole into a shell that just
opened:

- Blocks that use repo-relative paths start with their own `cd`. Copy the whole
  block, not the last line.
- `<angle brackets>` mark a value you substitute — `<ID>` a capture id, `<pid>`
  a process id, `<store>` the blob store id. Everything else is literal.

---

# The automated flow

Nobody types anything. A capture leaves the iPad and arrives on the customer's
dashboard on its own.

```
 iPad (CrateScanner)
   │  upload
   ▼
 Google Drive folder
   │  tools/pipeline_agent.py  (cron, every 10 min) — or drive_rgbd_autopilot
   │  any .zip → captures/inbox/CrateScan-<8HEX>.zip
   ▼
 tools/watch_inbox.py            RGB-D fuse → captures/status/CrateScan-<ID>.json
   │                                         demo_out/<...>/asset.json + dims.json
   ▼
 tools/watch_3dgut_queue.py      routes to TRELLIS or 3DGUT, records the verdict
   │                             in captures/status/.gpu_route_done.json
   ▼
 (optional, by hand) tools/splat_to_cad.py → cad_out/,  assetpipe sim-export → sim_out/
   │
   ▼
 tools/pipeline_agent.py  step 3b
   │  tools/autopublish.py       decides WHAT is ready, keeps the ledger
   ▼
 tools/publish_dashboard.py --merge     uploads + merges the manifest
   │
   ▼
 Vercel Blob (private)  ──►  web/ on Vercel  ──►  customer
```

The cron entry that drives all of it — already installed, one line, no secrets:

```cron
*/10 * * * * cd /home/jaeahn-jammy/3dasset && /home/jaeahn-jammy/miniconda3/envs/assetpipe/bin/python tools/pipeline_agent.py >> logs/agent/cron.log 2>&1
```

## When is an asset "finished"?

`autopublish` publishes nothing it is not sure about. All four gates must pass:

1. **It is a real capture.** The id has to be 8 hex — that is what
   `publish_dashboard.scan_key` produces for a CrateScan. Everything else is
   `scan_key`'s directory-name fallback: `runs`, `mouse`, `box_demo`,
   `bench_A7C9_fixed`, `crate_20260730_scooter`. Those are internal benchmark
   and debug output and must never reach a customer. The id shape *is* the
   allowlist — there is no second list to keep in sync.
2. **A completion marker exists** — one of `asset.json`, `dims.json`,
   `cad_report.json`, `sim_export.json`. These are not markers invented for the
   dashboard: `rgbd_object_asset` dumps `asset.json` and `dims.json` **last**,
   after the ply/glb/splat they point at, so their presence already means "this
   stage finished". `route_decision.json` is deliberately not a marker — the
   router writes it *before* the route runs.
3. **The GPU route agreed.** If `captures/status/CrateScan-<ID>.json` exists,
   then `captures/status/.gpu_route_done.json` must carry `CrateScan-<ID>` with
   `ok: true`. No entry means still queued or mid-train; `ok: false` means the
   route failed and the asset is incomplete.
4. **The output tree is quiet** — nothing under the asset's `demo_out/`,
   `cad_out/`, `sim_out/` directories has been touched for 300 s. A 3DGUT train
   writes for tens of minutes while cron ticks every 10, and the `.splat` export
   itself takes seconds. Without this the customer downloads a truncated file
   that opens as garbage.

The quiet check walks the **whole** tree, not just the files the publisher would
ship. During training the only things moving are checkpoints under
`3dgut_runs/`, which the publisher's allowlist skips — look at the deliverables
alone and an active training run appears perfectly idle.

It is checked **twice**: once when the asset is classified, and again with the
publish lock held, immediately before the upload starts. Gate 4 was a single
sample, and everything after it — reading the token, waiting for the lock, then
a publisher run that may stream for 45 minutes — happened later with nothing
re-walking. A write that starts inside that window is uploaded mid-write: the
uploader hands `requests` an open file and Content-Length comes from `fstat` at
request time, so a file being rewritten in place is PUT short. That does not
heal on the next tick either, because the publisher's upload cache stores the
URL under the hash of the file's **final** content and then skips the re-upload.
An asset whose tree moved in that window is deferred to the next run; one whose
tree moves *during* the upload is not recorded as published, and the report says
to re-send it with `--force-upload`.

> **`--quiet-seconds` moves gate 4, and `--quiet-seconds 0` deletes it.**
> `autopublish.py --quiet-seconds 600` (it takes a float; default 300) sets how
> long an output tree must be untouched, for both checks. At 0 every check
> passes, including the one taken with the lock held, so an asset mid-write
> uploads mid-write — the truncated-download path in the two paragraphs above,
> which the upload cache then makes permanent. There is no honest reason to
> lower it on this box; a 3DGUT train writes for tens of minutes. If a
> *specific* asset is finished and you know it, `--force <ID>` overrides the
> timer for that id alone and says so in the log, which is the narrow tool.
> `--quiet-seconds 0` is the wide one, and it applies to everything in the run.

> **Trap:** `captures/status/.3dgut_queue.lock` looks like a "GPU is busy"
> signal and is not. `watch_3dgut_queue` takes it once at startup and releases
> it in a `finally` when the process exits, so with `--iterations 30000` it is
> held permanently. Gating publishing on it would mean autopublish never ran.
> The per-asset `.gpu_route_done.json` entry is the real completion signal.

## First run publishes nothing, on purpose

The first time `autopublish` runs it records every finished asset as a
**baseline** and uploads none of them. Same reasoning as `pipeline_agent`'s
first-run stampede guard: there are ~16 finished captures on this box, only 3
were ever published, and the rest include hand-made test fixtures. Automation
that ships its whole backlog on first contact is an accident, not a feature.

From then on: anything that newly finishes publishes itself, and anything
reprocessed publishes itself again (its content signature moves). The historical
backlog stays a human decision — `--force <ID>`.

## Idempotency

`logs/agent/publish_ledger.json` — `id → {signature, sig_version, files,
updated, published_at, status}`. The signature is a sha256 over each
deliverable's `(path, size, mtime)` **plus the hero image's**, not over the
bytes: hashing 390 MB every 10 minutes to answer "did anything change" would be
absurd, and `publish_dashboard` does its own content hashing at upload time to
decide what to actually send. A rerun with nothing new is a silent no-op that
exits 0.

The hero image has to be in there. `gather()` keeps it in `preview_src`, which
is *not* one of the asset's `files` — those come only from the `DELIVERABLES`
patterns — and the two do not land together: in
`demo_out/CrateScan-7C3DD25E_3dgut` every deliverable was written at 19:10:59
and `render_check/contact_sheet.png` at 20:08:27, 57 minutes later. Hash the
deliverables alone and an asset published in that gap keeps a preview-less card
forever, because the preview arriving never moves the signature.

`sig_version` says which scheme a record was written with. Adding the hero image
moved the stored digest of every asset that has one, and "moved" means
"publish" — measured on the ledger as it stood, that was 5 of the 15 baselined
records (`7C3DD25E`, `DC22F084`, `76B73843`, `72D6C287`, `2A92CC43`), which
would have republished themselves on the first tick after the upgrade for no
reason. A record still on v1 whose old-scheme digest still matches is
**restamped, not republished**; one that fails that test really did change and
publishes normally.

**A ledger entry means the asset published in full.** Ids with any failed file
are left out on purpose, so the next tick retries them — see the exit-code table
and the partial-publish recovery below.

---

# One-time setup

Two places, both done by a human, once: **this box**, which needs a token to
publish, and **the deployment**, which needs three variables to serve. Each has
its own check, and both checks are commands you can run right now — see "Verify
the setup" and "Verify the deployment" below.

### 1. The blob token — on this box

It **cannot** be recovered from anything on this box — `vercel env pull` redacts
secrets, so `web/.env.local` has `BLOB_READ_WRITE_TOKEN` as an empty string and
`NEXT_PUBLIC_MANIFEST_URL` as the literal text `[SENSITIVE]`.

Get it from the Vercel dashboard ▸ Storage ▸ Blob store `store_sf4PvVI6x7HEvyHw`
▸ reveal/copy the `vercel_blob_rw_…` value. Then:

```bash
mkdir -p ~/.config/3dasset && chmod 700 ~/.config/3dasset
install -m 600 /dev/null ~/.config/3dasset/blob.env
${EDITOR:-nano} ~/.config/3dasset/blob.env
```

(`$EDITOR` is unset in this box's shells, so the bare `$EDITOR <file>` form
tries to *execute* the file and dies with `Permission denied`, rc 126, having
opened nothing. `${EDITOR:-nano}` falls back to an editor that is installed.
`autopublish`'s own setup hint still prints the bare `$EDITOR` form.)

Paste these two lines:

```sh
BLOB_READ_WRITE_TOKEN=vercel_blob_rw_...
DASHBOARD_MANIFEST_URL=https://sf4pvvi6x7hevyhw.private.blob.vercel-storage.com/dashboard/manifest.json
```

**Both lines, not just the token.** `autopublish` forwards
`$DASHBOARD_MANIFEST_URL` to the publisher as `--manifest-url` and has no flag
of its own for it. Omit that line and, until some run has recorded a real URL in
`.publish_cache/`, the publisher is guessing where the published manifest lives
— and a guess that misses aborts the merge with rc 3 on every tick, forever.
See "The publisher's own exit codes" below for what that looks like and how to
get out of it.

Use an editor. Do not `echo` the token onto a command line — it lands in
`~/.bash_history`. Do not put it in the crontab line either: `crontab -l` output
ends up in shell history and in agent reports people paste around.

The file lives **outside** the repo deliberately. A gitignored in-repo file
still gets swept into `--uploader local` copies and result zips, and still loses
to a `git add -f`.

Search order, first hit wins: `$DASHBOARD_ENV_FILE`,
`~/.config/3dasset/blob.env`, `<repo>/.blob.env`. A value already exported in
the environment always beats the file, so debugging with a different token is
never silently overridden.

#### Verify the setup

Still part of step 1. It uploads nothing and touches nothing the site, the
ledger or the publish cache reads — only its own `logs/agent/autopublish.log`
and `logs/agent/autopublish_last.json`:

```bash
cd /home/jaeahn-jammy/3dasset
/home/jaeahn-jammy/miniconda3/envs/assetpipe/bin/python tools/autopublish.py \
    --dry-run --verbose ; echo "rc=$?"
```

It loads the env file exactly as a real run does and prints a `[dry-run]
publish environment` block: which file it read (or that none of the three
exists), whether each of the two keys is present and where it came from — the
file, or an `export` in this shell — and the manifest URL. **The token value is
never printed**, by this or by anything else here; presence and provenance
only.

**The exit code is the verdict on this box**, and it is the code a *real* run
would fail with, so this check and cron can never give two different answers
about the same box:

- **rc 0**, `VERDICT: READY` — token and manifest URL both resolved. A real
  publish would work. This is the pass.
- **rc 0**, `VERDICT: READY WITH WARNINGS` — it would publish *successfully*
  and do the wrong thing. Read the `WARNING` line above it: either the token is
  not shaped like a blob RW token, or `DASHBOARD_MANIFEST_URL` does not point at
  `/dashboard/manifest.json`, which is where the publisher PUTs the manifest — a
  404 on an explicitly given URL is trusted as "empty dashboard", so the next
  real publish would replace the catalogue with just its own assets.
- **rc 2**, `VERDICT: this box CANNOT publish` — two ways in. **No token:** a
  real run exits 2 too, before the publisher is started, so nothing reaches the
  dashboard. **A value that is not a token:** a blob RW token is
  `vercel_blob_rw_<store>_<secret>` and the store id is read back out of it, so
  pasting the store id here gets *past* the token check on a real run and then
  fails every upload — which is exactly why it is caught here instead.
- **rc 1**, `VERDICT: this box CANNOT publish safely` — a token but no manifest
  URL, from either the env file or `.publish_cache/`. That is the rc-3-forever
  loop below, caught before it starts.

The environment block prints even when nothing is ready to publish, which on a
healthy box is most ticks. It used to print never: the dry run returned at
"nothing to publish" *before* it ever read the env file, so a missing token or a
missing `DASHBOARD_MANIFEST_URL` line passed this check clean and surfaced ten
minutes later as a cron failure.

A failed check prints the exact lines to add and to which file. Fix, re-run,
and go once it says `READY` — the next cron tick then publishes on its own.

### 2. The deployment's environment variables

Three are **required**. The site is not partly broken without one of them — it
is empty, or it is dark, or (before the gate was made to fail closed) it was
public. The blob token on this box, from step 1, is a *different* variable on a
different machine; the deployment needs its own copy.

| Variable | Read by | Missing ⇒ |
|---|---|---|
| `NEXT_PUBLIC_MANIFEST_URL` | `web/lib/manifest.ts` | falls back to `/manifest.json`, which `web/.gitignore` keeps out of the repo, so it is not in the build — `getManifest()` returns null and **the dashboard renders empty**, however well publishing worked. |
| `BLOB_READ_WRITE_TOKEN` | `web/lib/blob.ts` | every blob read goes out with no `authorization` header, and a private store answers 403 — **empty dashboard again**, plus `/api/download` returning `502 {"error":"blob 403"}` on every file. |
| `DASHBOARD_PASSWORD` | `web/lib/auth.ts` | `authState()` is `unconfigured`: `web/middleware.ts` answers **503 on every path** (including `/login` and `/api/login`) with a page naming this variable, and `/api/download` answers 401. Nothing is served to anyone. |

`AUTH_SECRET` is **optional** and changes one thing: it is the HMAC key for the
session cookie, and without it that key is `DASHBOARD_PASSWORD` itself. So with
it unset, rotating the passphrase invalidates every live session immediately;
with it set, sessions minted under the old passphrase stay valid for the rest
of their 12 h. Changing `AUTH_SECRET` logs everyone out either way. It is set
on this project.

`DASHBOARD_ALLOW_OPEN_ACCESS=1` is a **local-development hatch only** — it also
requires `NODE_ENV=development`, which `next build` never produces, so setting
it on the Vercel project does nothing. It is what lets `npm run dev` serve
without a passphrase; a deployed build ignores it.

**Why `DASHBOARD_PASSWORD` is not optional.** The store is private, so
`/api/download` is the only route to the bytes — and it streams them using the
deployment's own `BLOB_READ_WRITE_TOKEN`. A deployment that has the token and
no passphrase is a working, world-readable copy of the customer's scans. That
state used to be served (`middleware.ts` opened with "no password configured →
let everyone through"); it now refuses with a 503 instead. Setting the variable
is still the only thing that makes the dashboard *usable*, and "will only give
access to permitted users" is the requirement it implements.

#### Verify the deployment

```bash
cd /home/jaeahn-jammy/3dasset/web
vercel env ls
```

Run it from `web/` — that is where `.vercel/` is; from the repo root the same
command exits with `Your codebase isn't linked to a project on Vercel`. Read
two columns, not one: the name **and** the environments it applies to. A
variable set only for Production is absent from every Preview deployment, and a
Preview deployment with `BLOB_READ_WRITE_TOKEN` but no `DASHBOARD_PASSWORD` is
exactly the case the 503 exists for. To add one:

```bash
cd /home/jaeahn-jammy/3dasset/web
vercel env add DASHBOARD_PASSWORD production
```

`vercel env add <name> [environment]` prompts for the value, so the passphrase
does not land in `~/.bash_history`. Repeat per environment you want to work.
**Adding or changing any of these only affects deployments built afterwards**,
so redeploy — the running deployment keeps the values it was built with. That
is doubly true of `NEXT_PUBLIC_MANIFEST_URL`, which is inlined into the bundle.

Then check what the site actually answers:

```bash
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' \
    https://web-jayahn-3302s-projects.vercel.app/
```

- `307 …/login` — the gate is up. This is the pass.
- `503` — no `DASHBOARD_PASSWORD` in that environment. The body says so too.
- `200` — the deployed build predates the fail-closed middleware. Redeploy.
- `302 https://vercel.com/sso-api?…` — **this is what it answers today.** That
  is Vercel's own Deployment Protection, not this app: the project is currently
  reachable only by members of the Vercel team, which is why no customer has
  ever hit the passphrase prompt. Turning that off is what puts the site in
  front of customers, and from that moment `DASHBOARD_PASSWORD` is the only
  thing standing between the open internet and the private store.

---

# Checking it is working

```bash
cd /home/jaeahn-jammy/3dasset

# every asset + why it is or is not ready
/home/jaeahn-jammy/miniconda3/envs/assetpipe/bin/python tools/autopublish.py --list

# ...plus: can this box publish at all?
/home/jaeahn-jammy/miniconda3/envs/assetpipe/bin/python tools/autopublish.py --dry-run -v

# how many assets the ledger has recorded as published
/home/jaeahn-jammy/miniconda3/envs/assetpipe/bin/python -c \
  "import json;d=json.load(open('logs/agent/publish_ledger.json'));print(len(d['assets']))"

cat  logs/agent/autopublish_last.json    # last run: ready / published / rc
cat  logs/agent/latest.md                # the agent's report for this tick
tail -100 logs/agent/cron.log            # raw cron output

tail -f logs/agent/autopublish.log       # what publishing has done — blocks; Ctrl-C
```

`--list` and `--dry-run` each rewrite `logs/agent/autopublish_last.json` with
their own summary (`"note": "list only"` and `"note": "dry run: …"`), so read
that file *before* running them, not after — otherwise you are reading your own
diagnostic instead of the last real tick.

`--list` is the one to reach for. Every asset gets a verdict and, when skipped,
the reason: `not a capture id`, `no completion marker yet`, `GPU route queued or
in flight`, `GPU route failed`, `still being written`, `already published,
unchanged` (sometimes with `ledger record migrated to signature v2` — see
Idempotency). A `--force` that ends up skipped prints its own line, so it is
never silent.

`autopublish.log` rotates itself at 2 MB (`.log.1`). `cron.log` does not — it is
already ~700 KB and will need a `logrotate` entry eventually.

Exit codes, which is what `pipeline_agent` reports on:

| rc | meaning | under `--dry-run` |
|---|---|---|
| 0 | nothing to do, published fine, or seeded the baseline | a real publish would work (`READY`) |
| 1 | `publish_dashboard` failed, timed out, or refused to merge — **nothing** recorded, retries next tick. This is *four* different publisher exit codes collapsed into one; the report's quoted last line says which | a token, but no manifest URL — this box *would* hit that refusal, every tick |
| 2 | no blob token — ledger untouched, retries the moment the file appears | no token, or a value that is not one |
| 4 | publish lock wedged (live pid holding it > 2 h) — needs a human | — |
| 5 | **partial publish** — some files failed; those ids are not recorded and retry next tick, the rest are recorded. Raised as an anomaly, so escalation fires | — |

There is no autopublish rc 3. The publisher's rc 3 arrives here as rc 1.

A `--dry-run` records nothing either way; its exit code is a verdict on this
box's *configuration*, deliberately the same code a real run would fail with.
See "Verify the setup".

rc 5 is the one that used to be invisible. `publish_dashboard` returned 0 as
long as *one* file uploaded, autopublish recorded the whole batch, and since a
failed upload never changes the source tree the signature never moved again —
"already published, unchanged", forever, with the customer's downloads missing.
Three independent signals now have to agree before a batch is recorded: the
exit code, the `failures` count in the manifest the publisher just wrote, and
the ids named on its failure lines. Failures that name no asset disqualify the
whole batch.

## The publisher's own exit codes (most of them arrive as rc 1)

`autopublish` records a batch only for publisher rc 0 (clean) and rc 5
(partial). Every other code — 1, 2, 3, 4, a timeout kill, or anything it does
not recognise — records **nothing** and becomes autopublish rc 1, which
`pipeline_agent` reports as `autopublish exited 1: <last stderr line>`. The
publisher's own code never reaches the agent report, so that quoted line, and
the `!` lines in `logs/agent/autopublish.log`, are what tell these apart:

| `publish_dashboard` rc | meaning | what it means for you |
|---|---|---|
| 0 | every file is in the store and the manifest was published | — |
| 1 | nothing published: no asset matched `--only`, or every upload failed, or the manifest PUT itself failed. The live manifest is left intact | usually transient; the next tick retries |
| 2 | usage error (argparse's own code), including `no blob token` under `--uploader vercel` | autopublish checks for a token first and exits 2 itself, so this from cron means a token *is* set and the publisher rejected the invocation |
| 3 | `MERGE ABORTED` — the published manifest could not be read, so the prior state is unknown. Nothing was written, locally or remotely | **does not clear itself** — see below |
| 4 | `MERGE ABORTED` — the merge itself would have dropped an asset. Nothing written | a bug, not an operator error — see below |
| 5 | `PARTIAL PUBLISH` — the manifest was published but some files failed | autopublish's rc 5; handled above |

**rc 3 is the one that retries forever.** Merging needs to know *where* the
published manifest lives. The publisher takes the first of: `--manifest-url` /
`$DASHBOARD_MANIFEST_URL` (explicit), the URL the last successful run recorded
in `.publish_cache/<prefix>.json` (recorded), or `derive_manifest_url()`'s guess
built from the token + `--prefix` + `--access` (derived). A 404 on an explicit
or recorded URL genuinely means "nothing published yet" and the merge proceeds
onto an empty catalogue. A 404 on a **derived** URL is ambiguous — a wrong guess
(rotated token, a different store-id encoding, `--access` not matching how the
store was created) 404s exactly like an empty dashboard, and merging onto `{}`
would publish this run as the entire catalogue and erase every other asset from
the site. So the publisher refuses instead.

The realistic way to land there: `blob.env` has the `BLOB_READ_WRITE_TOKEN=`
line and not the `DASHBOARD_MANIFEST_URL=` one, on a box whose `.publish_cache/`
has no recorded URL yet (fresh checkout, or the cache was deleted). autopublish
has nothing to forward, the guess 404s, and every 10-minute tick aborts
identically and escalates. Recovery, in order:

1. Add the `DASHBOARD_MANIFEST_URL=` line to `~/.config/3dasset/blob.env`
   (setup step 1). This is the fix in almost every case and it is the fix for
   the cron path — the next tick publishes on its own, nothing to re-run.
2. If you do not know the URL, it is the Vercel project's
   `NEXT_PUBLIC_MANIFEST_URL` — the URL the site itself fetches, so the
   authoritative one. `cd /home/jaeahn-jammy/3dasset/web && vercel env ls` names
   it but prints its value as `Encrypted`, and `vercel env pull` redacts it the
   same way — so read it in the Vercel dashboard, project ▸ Settings ▸
   Environment Variables. What the same `env ls` output *does* print in the
   clear is `BLOB_STORE_ID`; take that id, drop the `store_` prefix, lowercase
   it, and the manifest is at
   `https://<id>.private.blob.vercel-storage.com/dashboard/manifest.json`. That
   last one is a reconstruction — the identical guess `derive_manifest_url()`
   makes, wrong in exactly the cases it is wrong in — so prove it before writing
   it into the env file. Pass it once and read the `merging onto N published
   asset(s)` line:

```bash
cd /home/jaeahn-jammy/3dasset
/home/jaeahn-jammy/miniconda3/envs/assetpipe/bin/python tools/publish_dashboard.py \
    --uploader vercel --access private --merge \
    --manifest-url https://<store>.private.blob.vercel-storage.com/dashboard/manifest.json \
    --only DC22F084
```

3. Only if the dashboard has genuinely never had anything published to it, and
   only for **one** id you have checked is a real capture — `--list` names them:

```bash
cd /home/jaeahn-jammy/3dasset

# pick an 8-hex id that says PUBLISH or "already published, unchanged"
/home/jaeahn-jammy/miniconda3/envs/assetpipe/bin/python tools/autopublish.py --list

/home/jaeahn-jammy/miniconda3/envs/assetpipe/bin/python tools/publish_dashboard.py \
    --uploader vercel --access private --merge --first-publish --only DC22F084
```

That single run records the URL the store actually returned in
`.publish_cache/dashboard.json`, which is all the bootstrap has to achieve. From
there every other asset goes back through `autopublish` — the next cron tick, or
`--force <ID>` for the historical backlog — so the four gates apply to all of
them.

**`--only` is not optional in that command, and it is a safety gate.**
`publish_dashboard` has no equivalent of autopublish's gate 1: `gather()`
collects every asset on this box — 26 as this is written — and gate 1 rejects 8
of them as internal benchmark and debug output (`mouse`, `mouse_ewa_regen`,
`runs`, `box_demo`, `bench_A7C9_fixed`, `bench_4179_fixed`,
`crate_20260730_scooter`, `crate_20260804_17_11_23` — whatever `--list` marks
`not a capture id`, which is the list that stays current). Run `--first-publish`
with no `--only` and, since it is by definition the *first* publish with nothing
to carry forward, all of those become part of the entire published catalogue. `--only` is the only filter the publisher has, so the
recovery is scoped to one id and the rest goes through autopublish.

`--first-publish` says "accept a 404 from a guessed URL as an empty dashboard".
It is a claim about the world, not a retry flag: if the guess was simply wrong
and a manifest *does* exist, this run becomes the whole catalogue and every
asset that was listed disappears from the site, exactly as `--no-merge` would.
Never reach for it to silence a repeating rc 3 — fix the URL. After one
successful run the publisher records the URL the store actually returned in
`.publish_cache/<prefix>.json`, so the guess path stops being used.

**rc 4 should be unreachable.** It fires only when the merge was about to drop
an id that was in the published manifest, which the code treats as its own bug.
Do **not** answer it with `--no-merge`: that carries out the removal the guard
just refused. Save the `MERGE ABORTED: merge would remove ...` line and the
current `web/public/manifest.json`, and fix the merge.

## Forcing a republish

```bash
cd /home/jaeahn-jammy/3dasset

# one asset
/home/jaeahn-jammy/miniconda3/envs/assetpipe/bin/python tools/autopublish.py \
    --force DC22F084

# several
/home/jaeahn-jammy/miniconda3/envs/assetpipe/bin/python tools/autopublish.py \
    --force DC22F084 --force 76B73843
```

`--force` overrides gates **3 and 4** — a queued or failed GPU route and the
quiet timer — plus the ledger, so an already-published asset republishes. That
is the escape hatch for the historical backlog and for an asset whose 3DGUT
train failed but whose RGB-D deliverables are perfectly good.

It does **not** override gates 1 and 2, and that is deliberate: gate 1 is the
only thing keeping internal benchmark output (`mouse`, `box_demo`,
`bench_A7C9_fixed`) off a customer dashboard, and gate 2 is the only evidence
that the stage which writes the deliverables actually finished — forcing past it
would ship whatever half of the asset exists. Forced ids are also uppercased
before matching, so a lowercase directory name can never match one anyway. A
`--force` blocked by either gate now prints
`--force <ID>: NOT published — <reason>` instead of skipping silently, and a
`--force` id that matches no asset at all says so too.

Always pass the **full 8-hex id**. `publish_dashboard --only` is a substring
match against the uppercased key, so a fragment sweeps in unrelated assets.

To republish everything the ledger has already seen, delete the ledger — but
note that a missing ledger triggers the baseline seed, which publishes nothing.
Deleting it forgets history; it does not re-upload.

## Publish manually

The real command, the one automation runs:

```bash
cd /home/jaeahn-jammy/3dasset
set -a; . ~/.config/3dasset/blob.env; set +a
/home/jaeahn-jammy/miniconda3/envs/assetpipe/bin/python tools/publish_dashboard.py \
    --uploader vercel --access private --merge --only DC22F084
```

`publish_dashboard` reads the token from the environment but, unlike
`autopublish`, does not know about the env file — hence the `set -a` source,
which is also why this is written that way rather than as `export
BLOB_READ_WRITE_TOKEN=vercel_blob_rw_...`: typing the token at a prompt puts it
in `~/.bash_history` just as surely as `echo`ing it into a file does. Sourcing
the file also exports `DASHBOARD_MANIFEST_URL`, which is what `--manifest-url`
defaults to, so the merge below fetches the authoritative manifest rather than
a guess.

`autopublish` builds exactly this, one `--only` per ready id, plus
`--manifest-url $DASHBOARD_MANIFEST_URL` when that variable is set (it never
puts the token on argv — `ps` is world-readable and the command line lands in
`cron.log`). It is built in one place and both executed and previewed from
there, so `autopublish --dry-run`'s `[dry-run] command:` line is the command,
not a paraphrase of it.

There is also a `--token` flag, equivalent to `$BLOB_READ_WRITE_TOKEN`. **Do not
use it.** It is the one way to get the token onto a command line, where `ps`,
`~/.bash_history` and any log of the invocation will pick it up; sourcing the
env file, as above, does the same job without that.

`--merge` fetches the live manifest, replaces only the records this run rebuilt,
and keeps the rest verbatim. In *that* command it is redundant — with neither
`--merge` nor `--no-merge` given, the publisher turns merging on by itself when

> `--only` was used **and** (the uploader is `vercel` **or** a manifest URL is
> known, from `--manifest-url` or `$DASHBOARD_MANIFEST_URL`)

which is exactly the shape above. A full rebuild (no `--only`) replaces the
manifest, which is what a full rebuild is *for*, and `--uploader local` keeps
working with no remote manifest to fetch. Pass `--merge` anyway, as automation
does: it keeps the intent true if someone later drops `--only` or changes
uploaders.

**`--no-merge` is the destructive one.** With `--only` it replaces the remote
manifest with just the selected assets, and every other published scan silently
vanishes from the dashboard — the blobs survive, but nothing lists them, and
recovery is a full ~400 MB re-upload. The publisher prints one warning, which
names whichever of the two causes is actually in play (`WARNING: --only without
merge (--no-merge was passed) — the manifest about to be written lists ONLY
…, and every other asset disappears from the dashboard`), and does it. There is
one honest use: deliberately truncating the catalogue to exactly what this run
built.

Other useful forms:

```bash
cd /home/jaeahn-jammy/3dasset

# no cloud account needed — renders straight out of web/public/
/home/jaeahn-jammy/miniconda3/envs/assetpipe/bin/python tools/publish_dashboard.py \
    --uploader local --dest-dir web/public/assets --base-url /assets

# what would go, and how big
/home/jaeahn-jammy/miniconda3/envs/assetpipe/bin/python tools/publish_dashboard.py \
    --dry-run
```

Both of those write `web/public/manifest.json` — the local uploader with
`/assets/...` URLs, over the record of what is in the blob store; `--dry-run`
sidesteps it by writing `manifest.dry-run.json` instead. `--manifest-out <path>`
points that write somewhere else, which is the clean way to build a manifest
without disturbing the one the automation reads back (see "Files the automation
owns"). Note this is `publish_dashboard --dry-run`, which is about *file sizes*;
`autopublish --dry-run` is the one that checks whether this box can publish.

The local uploader has a second side effect that is easy to miss: it rewrites
`.publish_cache/dashboard.json` with *its* destination in the head, and a
ledger whose `dest` does not match is discarded wholesale on the next run. So a
local preview also throws away the recorded manifest URL and every
already-uploaded marker, and the next real publish re-uploads all ~400 MB with
only a derived URL to merge onto. Sending `--dest-dir`/`--manifest-out` to a
scratch directory does *not* spare you this — the cache file is named after
`--prefix`, not the destination — so add `--prefix preview` if you want a local
render on a box that also publishes for real.

## Deploy the site

```bash
cd /home/jaeahn-jammy/3dasset/web
npm install
npm run build && npx next start          # verify on http://localhost:3000
```

There is no `DASHBOARD_PASSWORD` locally unless you put one in `web/.env.local`
(it is set for Production only, so `vercel env pull` does not bring it down —
that pulls Development), and the gate refuses rather than
opens — every path serves the 503 page. `next start` is a production build, so
the dev hatch cannot help there: set the passphrase in `web/.env.local`. Only
`npm run dev` can use `DASHBOARD_ALLOW_OPEN_ACCESS=1 npm run dev`, because the
hatch also requires `NODE_ENV=development` and so cannot follow you into a
deployment.

```bash
cd /home/jaeahn-jammy/3dasset/web
vercel link                              # already linked on this box
vercel --prod
```

The Vercel CLI is already installed here (`vercel --version` → 58.4.4); the
`npm i -g vercel` that used to be on that line is only needed on a fresh
machine. Before the first deploy, and after any change to them, check the three
required variables — see "Verify the deployment" in setup step 2. A deployment
carries the variables that existed when it was built, so any change to them
means another `vercel --prod`.

---

# Recovering from each failure mode

**Nothing new appears on the site.**

```bash
cd /home/jaeahn-jammy/3dasset
/home/jaeahn-jammy/miniconda3/envs/assetpipe/bin/python tools/autopublish.py --list
```

The reason is printed per asset. If every asset says `already published,
unchanged`, the pipeline never produced anything new — look upstream at
`logs/watch_inbox.log` and `logs/watch_3dgut.log`.

**`no blob token` in the agent report.**
The token file is missing or has no `BLOB_READ_WRITE_TOKEN=` line. Create it as
in setup step 1, then confirm with the "Verify the setup" command — it prints
which file it read and which key was missing, and exits 0 only once a real
publish would work. The ledger was not touched, so the next cron tick publishes
on its own — nothing to re-run by hand.

**`GPU route failed` for an asset.**
Read `captures/status/.gpu_route_done.json` for the route and
`captures/status/CrateScan-<ID>.json` for the tail of the failure. Either fix
and reprocess, or, if the RGB-D deliverables are good on their own,
`--force <ID>`.

**An asset is stuck on `GPU route queued or in flight` forever.**
Pre-existing trap, worth knowing: `pipeline_agent` restarts
`watch_3dgut_queue` without `--backfill`, and `pending_jobs` drops any status
finished more than 600 s before boot. A restart therefore strands whatever was
pending when the watcher died. Restart it by hand with `--backfill`:

```bash
cd /home/jaeahn-jammy/3dasset
pkill -f tools/watch_3dgut_queue.py
nohup /home/jaeahn-jammy/miniconda3/envs/assetpipe/bin/python \
    tools/watch_3dgut_queue.py --folder-id 1DpLpm4m4TxUn_Vn9Rfhr0o39HKM-Xkbl \
    --interval 30 --iterations 30000 --backfill >> logs/watch_3dgut.log 2>&1 &
```

**`publish lock is wedged`.**
`logs/agent/autopublish.lock` is `<pid> <started>`. A lock held by a *live* pid
is never broken automatically, however old — killing a live uploader mid-flight
would leave a partial upload racing a manifest PUT, which is worse than waiting.
A dead pid's lock is cleared on the next tick without help. So:

```bash
cd /home/jaeahn-jammy/3dasset
cat logs/agent/autopublish.lock                                  # "<pid> <started>"
ps -fp "$(cut -d' ' -f1 logs/agent/autopublish.lock)"            # still uploading?
```

The file exists only while a publish is in flight, so `No such file` here means
there is no lock and the wedge is elsewhere. If the pid really is dead weight,
kill it by the number you just read — deliberately, not from a substitution:

```bash
cd /home/jaeahn-jammy/3dasset
kill <pid> && rm logs/agent/autopublish.lock
```

**A publish was interrupted (timeout, OOM, reboot).**
Nothing to clean up. A killed or failed run records **no** id, so every asset in
that batch is still "unpublished" and the next tick retries. `publish_dashboard`'s
content cache skips whatever already landed, so the retry is cheap.

**`PARTIAL publish` in the agent report (rc 5).**
Some files failed; the assets that own them were left out of the ledger, so the
next tick retries them by itself. Nothing to do on a one-off (a flaky PUT, a
502). If the same ids come back every tick the failure is permanent — read the
`!` lines in `logs/agent/autopublish.log` for the individual files and their
error. Common causes: a revoked token, a file that was deleted after
classification, or a blob size limit. Note that for an asset that was *already*
live, the publisher holds its published record rather than overwriting it with a
partial one, so the dashboard keeps the last good version while this repeats.

**An upload landed truncated.**
Only possible if the tree was written during the upload; autopublish reports
that id and refuses to record it. A plain retry will **not** fix it —
`publish_dashboard`'s cache keyed that blob's URL under the file's final content
hash and will skip it. Force the re-PUT:

```bash
cd /home/jaeahn-jammy/3dasset
set -a; . ~/.config/3dasset/blob.env; set +a
/home/jaeahn-jammy/miniconda3/envs/assetpipe/bin/python tools/publish_dashboard.py \
    --uploader vercel --access private --merge --force-upload --only DC22F084
```

**The dashboard lost assets.**
Almost certainly a `publish_dashboard` run with `--no-merge` (or with
`--first-publish` against a URL that was wrong rather than empty). Re-run with
`--merge` and one `--only` per asset that should be listed; the blobs were never
deleted, only the manifest that lists them.

**`autopublish exited 1: MERGE ABORTED: ...` in the agent report.**
The publisher could not establish what is currently published, so it wrote
nothing — the live manifest is intact and the dashboard is unharmed. It will
repeat every tick until the manifest URL is fixed, and no amount of waiting
clears it. See "The publisher's own exit codes" above.

**The site renders an empty dashboard.**
Two variables produce exactly this, so check both before looking anywhere else:
`NEXT_PUBLIC_MANIFEST_URL` (no URL, so the app looks for a `/manifest.json` that
is not in the build) and `BLOB_READ_WRITE_TOKEN` (a URL, but the private store
403s an unauthenticated read). `cd /home/jaeahn-jammy/3dasset/web && vercel env
ls` shows both, and the environment each applies to. See setup step 2.

**The site answers 503 "This dashboard is not configured".**
`DASHBOARD_PASSWORD` is not set in that deployment's environment. That is the
gate refusing to serve rather than serving to everybody; it is not a crash and
there is nothing to debug. Add the variable (setup step 2) and redeploy.

**A publish outran the 10-minute cron interval.**
Expected on a cold upload and harmless: the next tick's autopublish sees the
lock and no-ops. The one side effect is that the long tick writes its
`logs/agent/state.json` last and can overwrite the newer state written by ticks
that overtook it. The damage is bounded — the stampede guard re-checks
`captures/done/` and `captures/status/` on disk before pulling a zip again.

---

# What gets published

An **allowlist** (`DELIVERABLES` in the publisher), not "everything minus
exclusions". The first version collected by extension and swept up 3.6 GB of
training intermediates — TSDF scene meshes, 94 MB contact sheets, per-frame
JPEGs. A customer dashboard must never ship an intermediate by accident, so
anything not explicitly named is not published. The allowlist currently yields
399 MB across 26 assets — `publish_dashboard --dry-run` prints the live figure.

Raw `scene_gaussians.ply` is ~250 MB per scan and is **excluded by default** —
the `.splat` is the viewable one at a tenth the size. `--include-raw` ships it.

Hero images are downscaled to 1400 px on publish.

Dimensions are published exactly as the pipeline recorded them, and nothing in
the publish path adds precision. The real precision of these reconstructions is
**±1–2 inches** (~19 mm surface RMS; dimensions move 3.6–7.4 in across
reconstruction settings). `dims.json`'s `inches_0_25` field is a rounding
convention, not an accuracy claim — do not let any surface imply otherwise.

And some assets have no real-world scale at all. Those publish with
`metric: false` and with **no** `measure` field, which is the site's contract
for "offer no numbers here". Two ways in (`_has_metric_scale` in the publisher):
the `NO_METRIC_SCALE.txt` marker that `tools/rgb_only_trellis.py` ships beside
an RGB-only mesh, and — the one that actually bites — an asset whose geometry is
all generated (TRELLIS) with nothing proving scale either way. A missing marker
proves nothing, so "unknown" is treated as not metric: a normalized mesh
presented as metric is the expensive mistake, not the cautious one. The
`measure` field on a metric asset names the *exact* file the published
dimensions came from, so the browser's measuring tool loads that geometry rather
than a differently-trimmed solve of the same cloud that would contradict the
number printed beside it.

## File → tool matching

The point of the dashboard: the customer picks their app, not a file
extension. `TARGET_RULES` in the publisher is the single source of truth, and
the site just renders what the manifest says.

| Target | Files | Notes |
|---|---|---|
| Onshape / Fusion / SolidWorks | `*_mm.stl`, `*_lite_mm.stl`, `*.dxf` (shipped from `sections/`), `scale_check_100mm.stl` | millimetres; DXF goes into a sketch |
| Blender | `*_m.ply`, `*_cloud_m.ply`, `*.glb`, `*.usda` | metres, Z-up; USD needs Blender 3.5+ |
| MuJoCo | `*.xml` + `collision_*.obj` + `*_visual.obj` | MJCF needs the meshes beside it |
| Isaac Sim / Omniverse | `*.usda` | Z-up, 1 m/unit, RigidBody + colliders |
| ROS / URDF | `*.urdf` | single-link rigid prop |
| Web viewer | `*.splat`, `scene_gaussians.ply` (only with `--include-raw`) | opens in a browser, no install |
| Other | `*.obj`, previews, `*.json` | measurements and provenance |

Adding a format is one row in `TARGET_RULES`; the site needs no change.

## Grouping

Artifacts from one capture are merged across `demo_out/`, `cad_out/` and
`sim_out/` by scan id — `CrateScan-XXXXXXXX`, else a bare 8-hex token, else
provenance (`cad_report.json`'s `source_splat` tells you `cad_out/couch` came
from DC22F084), else the directory name.

Two traps worth keeping in mind if you touch `scan_key`: an all-digit run like
`20260730` is a **date**, not hex, and gave the scooter capture its own bogus
asset; and a case-insensitive `[A-F]` test on surrounding text matches the "c"
in "scooter", so the digit check must apply to the token itself.

`autopublish` leans on the same function rather than keeping its own copy — a
second implementation of `scan_key` would drift and start passing `--only` ids
that match nothing.

## Files the automation owns

| Path | What it is |
|---|---|
| `logs/agent/publish_ledger.json` | what has been published (id → signature, file count, timestamp) |
| `logs/agent/autopublish.lock` | `<pid> <started>`; live pid respected, dead pid cleared |
| `logs/agent/autopublish.log` | publish history, self-rotating at 2 MB |
| `logs/agent/autopublish_last.json` | last run summary, read by `pipeline_agent` |
| `web/public/manifest.json` | the manifest this box last built — and an **input** to publishing, not only an output. See below |
| `~/.config/3dasset/blob.env` | the token, mode 0600, outside the repo |
| `.publish_cache/` | `publish_dashboard`'s upload cache: blob URL per (dest, content hash), plus downscaled hero images. `dashboard.json` also holds the manifest URL the store returned last run — the fallback that keeps merging safe when `DASHBOARD_MANIFEST_URL` is unset |
| `captures/status/.gpu_route_done.json` | owned by `watch_3dgut_queue`; read-only here |

**`web/public/manifest.json` is read back by the automation, so hand-editing it
has consequences beyond the local copy.** Every real `publish_dashboard` run
writes it (`--manifest-out`, defaulting there; `publish_dashboard --dry-run`
writes `manifest.dry-run.json` beside it instead) — and then `autopublish` reads it
twice:

- its `stats.failures` is one of the **three** signals that decide whether a
  batch counts as published. Zero out that number and a partial publish can be
  recorded as a clean one, which is the silent-data-loss bug rc 5 exists to
  prevent. It is only trusted if the file is newer than the run that just
  finished, so a stale copy is ignored rather than believed.
- on a **first** run, its asset ids are what mark baselined records
  `published-before-automation` in the ledger. Delete the file before the first
  tick and that distinction is lost — everything is recorded as a plain
  `baseline`.

The `--uploader local` recipe under "Other useful forms" above also writes it,
with local `/assets/...` URLs over the record of what is in the blob store.
Fine for a local preview, wrong to leave behind — a real publish is what puts
the blob URLs back, so send `--manifest-out` somewhere else if you only want the
preview. (`autopublish --dry-run` never touches this file at all: it stops
before the publisher.)

`logs/`, `captures/` and `.publish_cache/` are all gitignored, as is
`web/public/manifest.json` (via `web/.gitignore`), so none of this reaches git.
`.publish_cache/` especially: it is this box's record of what it has already
uploaded, and in another checkout it would claim files are live that were never
sent.

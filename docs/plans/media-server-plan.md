# Media Server — Action Plan (v2, updated with your decisions)

| Field | Value |
|---|---|
| Document Type | Implementation plan (server + client sync) |
| Status | **Ready for approval** (decisions folded in) |
| Target | `v0.7.0` |
| Host | `bots@192.168.1.9` (`webserver`, Ubuntu 22.04) |
| Public host | **`lca.budaslounge.com`** |
| Related | [`media-manifest-spec.md`](media-manifest-spec.md), [`music-integration-plan.md`](music-integration-plan.md) |

## Implementation status

| Piece | State |
|---|---|
| Flask service (`/health`, `/music/manifest.json`, `/media/<path>`) | **live at https://lca.budaslounge.com**, verified from outside Cloudflare |
| Token gate (sha256 list, rotation, per-IP limits) | done — 401 without/with bad token, 403 over budget |
| Manifest generator + first manifest | done — 29 tracks, 537,612,726 bytes, metadata matched 29/29 |
| Media upload (513 MB) | done |
| Apache vhost | **enabled**; wildcard cert reused, no cert work |
| Client sync, quarantine, curves, fades, autoplay | done in 0.7.0 (167 tests) |
| systemd unit | **staged, not installed** — needs the root block in `server/README.md` (template must be copied as `lca-media.service`) |

Evidence (run from a machine outside the LAN, through Cloudflare):

```
PASS  health 200                     tracks=29 bytes=537612726
PASS  manifest without token 401     manifest with bad token 401
PASS  manifest with token 200        totals match, 29 tracks
PASS  media range request 206        Content-Range: bytes 0-1023/25647512
PASS  artwork downloads              68328 bytes
PASS  path traversal rejected 404
```

---

## 0. Decisions recorded

| # | Decision | Outcome |
|---|---|---|
| 1 | Hostname | **`lca.budaslounge.com`** — an umbrella for future data (assets, tooling, other misc exports), not a music-only host |
| 2 | Token model | Hashed token list with rotation + per-IP limits (see §4) — chosen for longevity, detailed below |
| 3 | Deletion semantics | **Explained in §5; needs your call** (three options, recommendation given) |
| 4 | Launch behaviour | **App checks the playlist on launch and downloads diffs; a "Sync now" button in Settings forces it** (see §8) |

Because of (1) the URL layout is namespaced from day one, so `/assets/…` or `/tools/…` can be added later
without moving anything.

---

## 1. What already exists on the box (surveyed read-only)

| Item | Finding |
|---|---|
| `/home/bots/LeagueLoader/loaderServer.py` | Flask + Flask-SocketIO service, log files with 7-day retention, `notify@` failure hook |
| `loaderServer.service` | systemd, `User=bots`, `EnvironmentFile=/etc/voicebots/loaderServer.env`, `Restart=always` |
| Apache | per-subdomain vhosts, `ProxyPass → 127.0.0.1:<port>`, `ProxyPreserveHost`, `X-Forwarded-Proto: https` |
| **TLS** | shared wildcard `*.budaslounge.com` (DNS-01, Cloudflare, auto-renew) — **no certificate work** |
| Cloudflare | terminates TLS, forwards Host to the origin over HTTP |
| Python | system `flask 3.1.2`; `waitress`/`gunicorn` absent → add `waitress` in a venv |
| Disk / ports | 270 GB free; 37999 + 38004–38016 taken → **38030**, loopback only |

---

## 2. Shape

```
Client (LeagueCastAssist.exe)
   │  HTTPS  lca.budaslounge.com  (Cloudflare → Apache :443 → 127.0.0.1:38030)
   ▼
Flask service (lca-media.service, user bots)
   ├── GET /health                          no token   service + library summary
   ├── GET /music/manifest.json             token      what the library should contain
   ├── GET /media/music/<file>              token      the bytes
   └── GET /media/artwork/<file>            token      the artwork
        backed by /home/bots/LeagueCastAssistServer/lca-media/{music,artwork}
   future: /assets/…, /tools/…              same service, same auth, same vhost
```

The client already implements the download mechanics (`music/catalog.py`: manifest fetch, size+sha256 verify,
atomic `.part`, byte progress, cancel). This plan adds the host, the gate, deletion handling and launch sync.

---

## 3. Endpoints

### 3.1 `GET /health` — no token
```json
{ "ok": true, "service": "lca", "api": 1, "started_at": "2026-11-02T18:30:00Z",
  "music": { "library_version": "2026.11.02", "tracks": 29, "bytes": 537612726,
             "artwork": 10, "manifest_etag": "…" } }
```
Used by uptime checks and by the app's Media Server tab ("server reachable · 29 tracks · 513 MB").

### 3.2 `GET /music/manifest.json` — token required
The document from [`media-manifest-spec.md`](media-manifest-spec.md) §3, unchanged, plus the endpoints that
let a client show a size before downloading:

```json
{
  "schema_version": 1,
  "library_version": "2026.11.02",
  "generated_at": "2026-11-02T18:30:00Z",
  "min_app_version": "0.7.0",
  "tracks_total": 29,
  "bytes_total": 537612726,
  "tracks": [
    { "file": "music/Bit Rush.mp3", "artwork": "artwork/arcade.jpg",
      "size_bytes": 6854056, "sha256": "…", "artwork_sha256": "…",
      "title": "Bit Rush", "artist": "League of Legends", "duration_seconds": 151.249,
      "playback_mode": "shuffle", "group_name": null }
  ]
}
```
`ETag` + `If-None-Match` on this route; `Cache-Control: no-cache` so a forced sync always revalidates.

### 3.3 `GET /media/<relative-path>` — token required
* `send_file(..., conditional=True)` → Range / 206, `ETag`, `Last-Modified`, so a cancelled 61 MB WAV resumes.
* Path validated against the media root exactly like the client validates manifest paths.
* `Cache-Control: public, max-age=31536000, immutable` (content is hash-addressed).
* Unknown path → `404` JSON; never a directory listing.

### 3.4 Errors
```json
{ "error": "unauthorized", "detail": "Missing or invalid token. Update LeagueCastAssist." }
```
`401` missing/unknown token · `403` rate-limited · `404` unknown path · `503` starting up · `426` app too old
(when `min_app_version` is enforced).

---

## 4. The gate — chosen for longevity, not cleverness

The client ships the credential, so this can never be real security. What *can* be durable is how tokens are
stored, rotated and revoked. Design:

| Layer | Choice |
|---|---|
| Token format | `LCA-<43 chars base64url>` (32 random bytes), same shape as the existing `BUDA-…` tokens |
| On the client | Default value of `catalog.token`; overridable in Settings → Media Server |
| On the server | `tokens.json` holds **sha256 hashes** only, each with `{ id, label, created_at, enabled }`. No plaintext token on disk, and a leaked file cannot be replayed |
| Check | sha256 of the presented token, looked up in the enabled set, `secrets.compare_digest` on the digest |
| Rotation | add the new token (enabled) → ship the app → after one release, set the old one `enabled: false`. Old builds get a clean `401` with "update LeagueCastAssist" |
| Revocation | flip `enabled` and `systemctl reload lca-media` (SIGHUP re-reads `tokens.json`); no redeploy |
| Abuse control | per-IP token bucket: 120 req/min and 8 MB/s sustained → `403`. In-memory, resets on restart |
| Logging | `ip, token id (not value), path, bytes, status, duration` per download |
| Why not HMAC-signed URLs | the signing secret would have to ship inside the exe as well; it adds code and rotation work without adding secrecy |
| Forward-compatible | same token gates future `/assets/…` routes; a token can be scoped later by adding `"scope": ["music"]` to its entry |

This is the option that "holds up": plaintext never on disk, revoke without a build, old clients keep working
until you retire their token, and one gate covers everything we add under `lca.`.

---

## 5. Add / remove behaviour — what "delete" means (your question)

The manifest is the source of truth for the **server-managed** part of the library. When a track disappears
from the manifest, three things could happen:

### Option A — quarantine (recommended)
The file is **moved** to `assets/media/_orphaned/music/<file>`, and its `songs` row is set to
`playback_mode='disabled'` (the schema already allows it), so it vanishes from the picker but the row — and
therefore play history and vote references — survive.

* A bad deploy cannot destroy a producer's local library: the bytes are still on disk, one folder over.
* Restoring a track later is instant (it is already local, just re-enabled).
* Costs disk equal to whatever was removed until someone cleans it up.
* Mid-stream safety: nothing disappears while the app is running; the row is disabled, playback of the
  current track continues to its natural end.

### Option B — delete outright
The file and the row are deleted. Cleanest disk usage, and the picker is automatically correct.
**Downside:** a mistaken manifest push deletes audio from every producer's machine, and play history rows
would either be orphaned or cascade away. Not reversible without a re-download.

### Option C — ignore orphans
Files that are no longer in the manifest stay on disk and stay playable locally. Nothing is lost, but the
library silently drifts from the server ("why do I still see a track we retired?"), and two producers see
different libraries.

**Recommendation: A.** It gives C's safety with B's correctness, and the "Clean orphaned files" button in the
download dialog lets anyone reclaim the disk on their own terms. If you prefer B, the only change is one
branch in the sync code — but I would not ship B before the first real release.

Regardless of choice: a disabled row is still shown in the library as `Disabled (removed from server)` until
the orphan is cleaned, so the change is visible rather than mysterious.

---

## 6. Files, service and vhost

```
/home/bots/LeagueCastAssistServer/
  server.py                  Flask: /health, /music/manifest.json, /media/<path>, token + rate limit
  make_manifest.py           walks lca-media/, writes manifest.json (sha256, size, duration from music.db)
  manifest.json              generated artifact, served from memory; regenerated on change
  tokens.json                sha256 hashes + labels + enabled flags (mode 0600)
  lca-media/music/*.mp3|wav  the 29 tracks (513 MB)
  lca-media/artwork/*.jpg    the 10 images
  .venv/                     python3 -m venv + waitress
  logs/lca_<stamp>.log       retention 14 days (loaderServer pattern)

/etc/lca.env                 LCA_MEDIA_ROOT, LCA_MEDIA_PORT=38030, LCA_TOKENS_FILE, LCA_LOG_DIR
/etc/systemd/system/lca-media.service        User=bots, Restart=always, OnFailure=notify@%n.service
/etc/apache2/sites-available/lca.conf        ServerName lca.budaslounge.com, ProxyPass → 127.0.0.1:38030,
                                             wildcard cert reused, ProxyTimeout 300 (long downloads)
```

---

## 7. Rollout

1. `rsync -av assets/media/ bots@192.168.1.9:/home/bots/LeagueCastAssistServer/lca-media/` (513 MB, LAN).
2. `make_manifest.py` → `manifest.json` (metadata taken from `music.db` so titles match the seed library).
3. Write service + env + `tokens.json` (generate token, keep a copy for the client default).
4. Enable `lca-media.service`; `curl 127.0.0.1:38030/health` on the box.
5. vhost + `a2ensite lca` + reload Apache; create the Cloudflare DNS record for `lca` (wildcard cert already
   covers it); verify `https://lca.budaslounge.com/health` from outside.
6. Verify: no token → 401; bad token → 401; good token → manifest; `Range: bytes=100-200` → 206; >120 req/min
   → 403; disabled token → 401.
7. Client: launch sync against the live server, then no-op sync, then a deleted-track sync.

---

## 8. Client-side work

| Change | Why |
|---|---|
| `CatalogSettings.token` | send `X-LCA-Token`; the release token ships as the default |
| `CatalogSettings.manifest_url` / `media_prefix` | fits the namespaced layout: manifest `…/music/manifest.json`, media `…/media/music/…` |
| **`catalog.sync_on_launch = True`** | on startup, fetch the manifest, show "Music: 29 of 29 tracks" in the status bar, download only diffs in the background worker |
| **"Sync now" button** (Settings → Media Server + `File → Music Media…`) | forces a re-check and download regardless of launch state |
| Deferred while a game is running | a launch sync during an in-progress game is postponed to the idle state, so bytes never compete with a cast |
| Orphan handling (§5) | quarantine + disable, plus "Clean orphaned files" in the dialog |
| `/health` probe in the dialog | "server reachable · 29 tracks · 513 MB" before the user commits to a sync |
| 401/403 messaging | "Your LeagueCastAssist is too old for this library — update the app" |
| Tests | token header, 401 surfaced, orphan quarantine, launch-sync decision (during game / idle) |

`catalog.auto_check` is renamed in spirit: the old default-false flag is replaced by `sync_on_launch: true`.

---

## 9. Bandwidth

Cold install = **537 MB per producer**, then only diffs. Because you want a launch sync, the first launch after
a fresh install is the expensive one; the app shows the total size before starting and can be cancelled
(partial downloads resume). A `starter → full` split stays on the table but is not planned for this release.

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| Launch sync competes with a cast | skipped/deferred while a game is detected; explicit button otherwise |
| Home upload saturated | diffs only after the first sync; resume via Range; 8 MB/s per-IP cap |
| Apache 60 s `ProxyTimeout` | set 300 s on this vhost |
| Cloudflare caching a stale manifest | `no-cache` + `ETag` on the manifest; media immutable |
| Bad manifest push | quarantine (Option A) keeps bytes + history |
| Token leak | hashed list, revoke via `enabled:false` + reload, per-IP limits |
| Old app after token retirement | `401` with an explicit "update the app" message; retire only after a release has shipped |

---

## 11. Effort

| Piece | Estimate |
|---|---|
| Flask service (3 routes, token file, rate limit) | 0.75 d |
| `make_manifest.py` + first manifest | 0.25 d |
| systemd + env + vhost + DNS + outside verification | 0.25 d |
| Media upload | 0.25 d |
| Client: token, URL layout, launch sync, Sync-now button, orphans, health, tests | 1.25 d |
| End-to-end verification (cold, no-op, delete, off-LAN) | 0.25 d |
| **Total** | **≈ 3 days** |

---

## 12. Open item

Only one: **§5 delete semantics** — A (quarantine, recommended), B (delete), or C (ignore). Everything else is
decided and ready to build.

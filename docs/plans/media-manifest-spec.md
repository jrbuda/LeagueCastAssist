# Media Manifest Specification — proposal for review

| Field | Value |
|---|---|
| Document Type | Interface specification (proposal) |
| Status | **Draft for review** — no server exists yet |
| Related | [`music-integration-plan.md`](music-integration-plan.md) §6 |
| Client implementation | `src/league_cast_assist/music/catalog.py` |
| Schema version | `1` |

---

## 1. Purpose

LeagueCastAssist ships **no audio**. The 29-track library (537 MB: 24 MP3 + 5 WAV) and 10 JPEG artworks
are hosted on a central server and downloaded beside the exe into `assets/media/`. This document defines
what that server must serve so the client can sync the library, verify it, and survive partial failures.

Design goals, in priority order:

1. **Boring to host.** Static files plus one JSON document. Any static host, CDN, or object store works.
2. **Idempotent for the client.** Every sync can be re-run; nothing is ever double-written.
3. **Verifiable.** A corrupted or truncated transfer must be detectable before it reaches the player.
4. **Cheap to update.** Adding one track must not force a full 537 MB re-download.
5. **Migratable.** Moving from the temporary domain to `thearchiveslol.com` must not require a client
   release beyond a settings change (and ideally not even that — see §9).

Non-goals for v1: authentication, per-user libraries, streaming playback, transcoding, deletion
propagation.

---

## 2. URL layout

```
{BASE_URL}/manifest.json                 <- the only endpoint the client needs
{BASE_URL}/music/Bit Rush.mp3
{BASE_URL}/music/Echoes in The Archives.wav
{BASE_URL}/artwork/arcade.jpg
```

* `BASE_URL` is a user-editable setting (`catalog.base_url`), e.g.
  `https://music.budaslounge.com/media` for now, later `https://media.thearchiveslol.com`.
* Every path inside the manifest is **relative to `BASE_URL`** and mirrors the on-disk media folder
  (`assets/media/music/...`, `assets/media/artwork/...`). That mirroring is what lets the existing
  `music.db` rows (`file_path = "music/Bit Rush.mp3"`) work unchanged.
* Files should be served with correct `Content-Type`, `Content-Length`, and `ETag`.
* HTTPS only. The application refuses plain `http://` except for `localhost` (development).

Filenames keep their current human-readable form, including spaces. Clients percent-encode on the wire,
so no server-side rewriting is required.

---

## 3. Manifest document

`GET {BASE_URL}/manifest.json` returns a single JSON object.

### 3.1 Top level

| Field | Type | Required | Meaning |
|---|---|---|---|
| `schema_version` | int | ✅ | Manifest grammar. Client supports `1`; unknown values are rejected with a clear message |
| `library_version` | string | ✅ | Opaque content version of the library (e.g. `2026.11.02`). Used for display and cheap change detection |
| `generated_at` | ISO-8601 string | ✅ | When the manifest was produced (UTC) |
| `min_app_version` | string | ⬜ | Oldest client allowed to consume this manifest (semver) |
| `tracks` | array | ✅ | Track objects (§3.2) |
| `artwork_only` | array | ⬜ | Optional standalone artwork entries not tied to a track (e.g. future theme art) |

### 3.2 Track object

| Field | Type | Required | Meaning / validation |
|---|---|---|---|
| `file` | string | ✅ | Media-root-relative path, forward slashes. Rejected if absolute, contains `..`, `:`, or is empty |
| `title` | string | ⬜ | Display title; falls back to the filename stem |
| `artist` | string | ⬜ | Display artist |
| `album` | string | ⬜ | Album name |
| `album_artist` | string | ⬜ | Album artist |
| `artwork` | string | ⬜ | Media-root-relative JPEG path, same validation as `file` |
| `song_id` | int | ⬜ | Existing library id. Omitted for new tracks (client keys on `file`) |
| `size_bytes` | int | ⬜ | Exact expected size. Recommended: enables a cheap pre-check |
| `sha256` | 64-hex string | ⬜ | Integrity digest. Strongly recommended |
| `artwork_sha256` | 64-hex string | ⬜ | Integrity digest for the artwork |
| `duration_seconds` | float | ⬜ | Used for the progress bar before the file is decoded |
| `playback_mode` | `shuffle` \| `manual` \| `disabled` | ⬜ | Default `shuffle`. Only applied when the row is **new** |
| `group_name` | string | ⬜ | e.g. `silver_scrapes`, `echoes`, `worlds_orchestral`. Only applied when new |
| `source_url` | string | ⬜ | Attribution link (not fetched) |

**Merge rule (important):** the manifest can create rows but never modifies existing ones. Play history,
repeat cooldowns, and Twitch votes stay authoritative on the client. A manifest change to
`playback_mode`/`group_name` for an existing `file_path` is ignored; changing those requires a client-side
action or a deliberate migration.

### 3.3 Example

```json
{
  "schema_version": 1,
  "library_version": "2026.11.02",
  "generated_at": "2026-11-02T18:30:00Z",
  "min_app_version": "0.6.0",
  "tracks": [
    {
      "song_id": 1001,
      "file": "music/Bit Rush.mp3",
      "artwork": "artwork/arcade.jpg",
      "size_bytes": 6854056,
      "sha256": "0f4c1d5a6d3c9f2e8b7a6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f",
      "artwork_sha256": "aa11bb22cc33dd44ee55ff6677889900aabbccddeeff00112233445566778899",
      "title": "Bit Rush",
      "artist": "League of Legends",
      "album": null,
      "album_artist": "League of Legends",
      "duration_seconds": 151.249,
      "playback_mode": "shuffle",
      "group_name": null,
      "source_url": null
    },
    {
      "song_id": 1013,
      "file": "music/Echoes in The Archives.wav",
      "artwork": "artwork/echoes.jpg",
      "size_bytes": 61586178,
      "sha256": "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809",
      "title": "Echoes from the Archives",
      "artist": "Bryce Hudgins",
      "duration_seconds": 174.56,
      "playback_mode": "manual",
      "group_name": "echoes"
    }
  ]
}
```

---

## 4. Versioning and change detection

| Mechanism | Use |
|---|---|
| `schema_version` | Hard compatibility gate. Client supports `1`; a future `2` requires client work, not a guess |
| `library_version` | Shown in the UI ("library 2026.11.02"), and the intended cheap "has anything changed?" signal |
| `ETag` / `Last-Modified` | Recommended. A conditional `GET` of the manifest makes a no-op launch a few hundred bytes |
| `min_app_version` | Refuses a manifest the client cannot interpret (e.g. new fields it must not silently drop) |

**Change detection is per file, not per library.** The client compares `size_bytes` first (cheap) and
`sha256` second (reads the file). A single new track therefore costs one file download, not 537 MB.

### Incremental-update rule

Additions and replacements are supported: a changed `sha256` for an existing `file` triggers a re-download
of that file only.

**Removals are not propagated in v1.** If a track disappears from the manifest, the client keeps the local
file and the library row. Deleting user-visible content because a server list changed is a destructive
action that needs an explicit product decision (see §10).

---

## 5. Client sync algorithm

```
GET {BASE} / manifest.json                    (conditional, if ETag known)
validate schema_version, min_app_version
for each track:
    jobs += (track.file, track.sha256, track.size_bytes)
    if track.artwork: jobs += (track.artwork, track.artwork_sha256, None)
for each job (index, total):
    if local file matches (size and/or sha256):  skipped += 1; continue
    if verify_only:                              failed += "missing or corrupted"; continue
    download to "<file>.part" (streamed, 256 KB chunks, cancellable)
    verify size + sha256
    atomic rename .part -> <file>
    downloaded += 1
upsert manifest tracks into the library (create-only)
report {downloaded, skipped, failed[], bytes_downloaded}
```

Behavioural guarantees the client already implements (§ test coverage in `tests/test_media_catalog.py`):

* Cancelling mid-sync aborts within one chunk and leaves **no** `.part` file behind.
* A corrupted or truncated file is detected by hash and re-fetched on the next sync; it is never accepted.
* A per-file failure does not abort the whole sync; it is reported and the rest continues.
* Sync is safe to run at any time: it is a verifier as much as a downloader.

### Progress reporting

Progress must be **byte-based**, not file-based: one 61.6 MB WAV is 11 % of the library and would otherwise
appear as a single instant step. Current UI: per-file label plus overall file count, with the file name
visible so a slow transfer is legible.

---

## 6. Integrity and failure handling

| Failure | Client behaviour |
|---|---|
| Manifest 404 / unreachable | Clear "could not reach the media server" message; nothing else changes |
| Manifest invalid JSON | Rejected with the schema error; local library untouched |
| `schema_version` newer than supported | Refused with a message to update the app |
| Hash mismatch | File rejected, one retry, then reported per file |
| Disk full / permission denied | Per-file failure reported; already-downloaded files stay valid |
| Server serves an HTML error page as `.mp3` | `size_bytes`/`sha256` mismatch catches it |

The client never deletes or overwrites library rows during a sync, so a bad manifest can at worst leave
tracks un-downloaded — never lose the user's history.

---

## 7. Security

* HTTPS only (except localhost during development).
* `BASE_URL` is user-editable, so it is treated as untrusted input: paths are validated against
  traversal (`..`, absolute paths, drive letters, `:`), and every download is written inside the media
  root only.
* No credentials, tokens, or query-string secrets in v1. If Downloads need protection later, prefer a
  signed-URL or `Authorization` header design rather than embedding a token in the manifest.
* The client treats manifest metadata as display text only — nothing in it is executed or used to build
  paths outside the media root.

---

## 8. Hosting notes

* Static hosting is enough: object store + CDN in front (Cloudflare/R2/S3 + CloudFront/Bunny are all fine).
* Serve `manifest.json` with `Cache-Control: no-cache` (or short `max-age`) plus `ETag`; media files are
  immutable per hash, so they can be `Cache-Control: public, max-age=31536000, immutable`.
* Enable HTTP compression on the manifest only (media is already compressed; WAV benefits, but CORS/CDN
  range behaviour matters more than size).
* Recommended companion artifact: `manifest.json.sha256` so the manifest itself can be mirrored safely.
* Total egress per full cold sync: ~538 MB. Consider a "lite" first-run option later (artwork-only or a
  starter pack) if the download proves too heavy — see §10.

---

## 9. Migration path (`budaslounge.com` → `thearchiveslol.com`)

The client's `base_url` is a plain setting, so migration is operationally free:

1. Serve the same tree at the new host.
2. Keep a permanent 301 from the old base URL.
3. Change the shipped default `catalog.base_url` in the next release; existing installs keep working via
   the redirect, or immediately once they update the setting.
4. Optional hardening: let the client accept a **list** of base URLs (primary + fallback) rather than one.
   If desired, that is a small change to `catalog.py` and a `catalog.fallback_urls: list[str]` setting —
   worth deciding before the domain lands.

---

## 10. Open questions for you

1. **Hosting shape** — plain static files behind a CDN (recommended, matches this spec), or an API?
2. **Auth** — public library, or gated downloads? Public keeps the client simple; gated needs a token
   design before v1.
3. **Artwork strategy** — ship the 10 full-size JPEGs (as today), or add a `artwork_thumb` variant for the
   strip/now-playing (faster first paint, more files)?
4. **Deletions** — should removing a track from the manifest ever delete it locally? Current answer: no.
5. **Initial download size** — 538 MB cold. Offer a "starter pack" (Silver Scrapes, Echoes, Worlds
   orchestral) first, or keep one archive-sized sync?
6. **Metadata authority** — after the first sync, is the manifest allowed to correct titles/artists
   (a merge field list), or is it strictly create-only as specified in §3.2?
7. **Manifest generation** — generated from the `music.db` seed at build time (so the DB stays the single
   source of truth), or authored independently?

Answers to 1–3 unblock the server work; 4–7 can be decided after your first round of QA.

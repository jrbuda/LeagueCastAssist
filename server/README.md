# lca media server

Serves the LeagueCastAssist music library at **https://lca.budaslounge.com**.

```
GET /                        no token   service banner
GET /health                  no token   service + library summary (tracks, bytes, etag)
GET /music/manifest.json     token      what the client library should contain
GET /media/<relative-path>   token      the bytes (Range/ETag aware)
```

Host: `bots@192.168.1.9` (`webserver`). App dir: `/home/bots/LeagueCastAssistServer`.
Apache terminates the public side, Cloudflare terminates TLS in front of that, and the wildcard cert
`*.budaslounge.com` already covers the hostname (no certificate work).

## Layout on the server

```
/home/bots/LeagueCastAssistServer/
  server.py            the Flask service (waitress WSGI)
  make_manifest.py     regenerates manifest.json from lca-media/ (+ music.db for metadata)
  issue_token.py       issue / disable tokens (stores sha256 only)
  verify.py            smoke test of all endpoints
  lca.env              runtime config (mode 600)
  tokens.json          token hashes, labels, enabled flags (mode 600)
  manifest.json        generated; served from memory, reload on SIGHUP
  lca-media/music/     29 tracks, 513 MB
  lca-media/artwork/   10 images
  logs/                lca_<stamp>.log, 14-day retention
  deploy/              lca.service, lca.conf, lca.env.example (templates)
```

## Install (root — one paste)

The service itself runs as `bots`; only the unit and the vhost need root.

```bash
sudo cp /home/bots/LeagueCastAssistServer/deploy/lca.service /etc/systemd/system/
sudo cp /home/bots/LeagueCastAssistServer/deploy/lca.conf /etc/apache2/sites-available/
sudo a2ensite lca
sudo systemctl daemon-reload
sudo systemctl enable --now lca-media
sudo apache2ctl configtest && sudo systemctl reload apache2
```

Then verify from any machine:

```bash
curl -s https://lca.budaslounge.com/health
curl -s -o /dev/null -w '%{http_code}\n' https://lca.budaslounge.com/music/manifest.json   # 401
curl -s -H "X-LCA-Token: $LCA_TOKEN" https://lca.budaslounge.com/music/manifest.json | head -c 200
```

## Day-to-day

| Task | Command |
|---|---|
| Add/replace media | copy into `lca-media/music/` or `lca-media/artwork/`, then regenerate and reload |
| Regenerate manifest | `python3 make_manifest.py --media-root lca-media --db music.db --out manifest.json --min-app-version 0.7.0` |
| Apply a new manifest/token list | `systemctl reload lca-media` (SIGHUP reloads manifest + tokens, no restart) |
| Issue a token | `python3 issue_token.py --issue --label "LCA 0.7.1"` |
| Retire a token | `python3 issue_token.py --disable --label "lca-0.7.0"` then reload |
| List tokens | `python3 issue_token.py --list` |
| Smoke test | `python3 verify.py --token <token>` |
| Logs | `tail -f logs/lca_*.log` |

Tokens are hashed; the plaintext is only printed once when issued. The client ships one as the default of
`catalog.token`, and rotation is "issue new → ship app → disable old after a release".

## Why the manifest matters

The client compares the manifest against `assets/media` on every launch (and on demand):

* new or changed `sha256` → download (size-checked, `.part` → verify → atomic rename)
* local file not in the manifest → moved to `assets/media/_orphaned/` and its library row set to `disabled`
  (never deleted automatically, so a bad manifest push cannot wipe a producer's library)

Manifest schema: `docs/plans/media-manifest-spec.md`. Server + client plan: `docs/plans/media-server-plan.md`.

# Packaging & Auto-Update — code dive and decision plan

| Field | Value |
|---|---|
| Document Type | Analysis + decision plan |
| Status | **For review** — recommend Option A + B for 0.6.0 |
| Related | [`music-integration-plan.md`](music-integration-plan.md) §10 |
| Trigger | "How did it work beforehand?" + "auto update is mandatory" |

---

## 1. Short answer: nothing changed, and nothing was broken on your machine

The released app has **always** been a PyInstaller *onefile* build, and every launch has extracted the
bundle into `%TEMP%`. You never had a problem because your machine allows it — and there is hard evidence of
that on disk.

| Evidence | What it shows |
|---|---|
| `git show v0.5.5:build.ps1` → `--onefile` for both `updater` and `LeagueCastAssist` | 0.5.5 shipped as a single self-extracting exe |
| `LeagueCastAssist.spec` → `runtime_tmpdir=None`, `a.datas` inside `EXE(...)` | onefile layout, extraction to the default temp dir |
| `release.yml` publishes `dist\LeagueCastAssist.exe` + `.sha256` | the published asset *is* that onefile exe |
| `%TEMP%` contained **86** `_MEI*` folders, oldest dated February 2026, one containing `python311.dll` | your installed app has extracted itself ~85 times and worked every time |
| Error dialog you photographed | produced by **my** launch attempt of the exe from the workspace volume on `E:` |

So: the integration did not introduce this, the shipped release has the same behaviour, and — as §1.1 shows —
the same binary starts normally from a folder on `C:`. The dialog was an artifact of *where* I launched the
exe from, not of what we built. I should have caught that before reporting a packaging problem.

### 1.1 Addendum — the failure reproduces for the *official* 0.5.5 build too

Follow-up testing (same machine, same session) changed the conclusion more than once, so the evidence is
recorded here in order:

| Test | Result |
|---|---|
| Download `v0.5.5` release asset from GitHub and launch it | **`Error` dialog, no `_MEI` created** — identical to our 0.6.0 build |
| Launch either exe via Task Scheduler (outside the agent's process tree, user session) | same `Error` dialog |
| Build the same app with PyInstaller 6.9.0 (older bootloader) | same failure — not a bootloader-version regression |
| `--runtime-tmpdir %LOCALAPPDATA%\LcaRT`, task-launched | `LOADER: failed to create runtime-tmpdir path … Access is denied` |
| Portable (`--onedir`) build run from the workspace (`E:`) | starts normally |
| **The same onefile exe copied to `C:\Users\thebu\LcaSmoke` and started from there** | **starts normally**: seeds `music.db`, logs CDragon downloads, window titled `LeagueCastAssist` |
| PowerShell (signed, `C:\Windows`) creating `_MEI*` folders in `%TEMP%` | succeeds |
| Unsigned PyInstaller exe creating a directory under `C:\Users\…` **while its own image is on `E:`** | denied |

Conclusion:

**Everything that failed was a build whose image lived on `E:` while `%TEMP%` lives on `C:`. Every build
whose image lived on `C:` worked.** The same binary, same launch method, only a different folder — the
restriction is on cross-volume writes for processes started from the workspace volume, i.e. a property of
this agent environment, not of the app or of Windows.

That also explains the earlier contradictions (why `cmd mkdir` in `%TEMP%` succeeded, why a onedir build
worked, why the official 0.5.5 failed here but works for the people running it).

## 1.2 Outcome: no product change

The single-file build is **not broken**, so there is nothing to fix and nothing new to ship. The portable
build type, the portable release asset, `is_portable_build()`/update guard, and the README section that
recommended a second download were all reverted; `release.yml`, `build.ps1` and `README.md` are back to the
0.5.x shape (onefile exe + `.sha256` only).

What remains from this investigation is the knowledge recorded here and in the risk list: a onefile app
extracts to `%TEMP%` before Python starts, so a genuinely broken `%TEMP%` (missing, full, redirected, or
blocked by security software) is the one environmental failure the app cannot recover from. That is
identical to every previous release.


## 2. Why my sandbox launches failed (and why that says nothing about your PC)

> Superseded by §1.1: the discriminator turned out to be the volume the exe lived on, not the temp root.
> The transcripts below are kept because they document the bootloader's failure mode.

I built a `--debug bootloader` probe to get the exact failure:

```
LOADER: application has onefile semantics...
LOADER: initializing security descriptor for temporary directory...
LOADER: attempting to create temporary application directory under C:\Users\thebu\AppData\Local\Temp\
LOADER: ERROR Could not create temporary directory!
```

Then ran the same exe with five different temp roots:

| Temp root | Result |
|---|---|
| `E:\code_stuff\LeagueCastAssist\.tmp\runtime` (workspace) | ✅ created `_MEI`, app started |
| `C:\Users\thebu\AppData\Local\Temp` | ❌ bootloader error |
| `C:\Users\thebu\AppData\Local\LcaRuntime` | ❌ bootloader error |
| `C:\LcaRuntimeTest` | ❌ bootloader error |
| `E:\LcaRuntimeTest` | ❌ bootloader error |

A plain `cmd /c mkdir` in `%TEMP%` succeeds from the same context, so it is not a permission problem on the
folder. The distinguishing step is that PyInstaller creates its `_MEI` directory **with an explicit
security descriptor** (`D:(A;;FA;;;S-1-5-21-…-1001)`); the sandbox permits that only inside the workspace.
That is a harness restriction, not an OS or product issue.

## 3. How auto-update works today (code dive)

```
app start (frozen)
  └─ update.install_update_after_exit(downloaded_exe)          update.py:267
       1. can_install_downloaded_update()  → win32 + frozen     update.py:248
       2. source = updates\LeagueCastAssist-vX.Y.Z.exe          update.py:186
       3. target = sys.executable                               update.py:275
       4. updater = <app_dir>\updater.exe                        update.py:252
            ← copied out of _MEIPASS on first use (bundled via --add-data)
       5. Popen(updater --install --source … --target … --pid …) detached
  └─ app quits

updater.exe (separate onefile PyInstaller build, updater.spec)
   a. wait ≤60 s for the app PID to exit (psutil, polling fallback)   updater.py:57
   b. delete stale .bak, rename target → target.exe.bak               updater.py:103
   c. move new exe → target (in place)                                updater.py:120
   d. Popen(target) detached, cwd = app dir                           updater.py:136
   e. delete .bak, log every step to updater.log                      updater.py:154
   f. on step-c failure: rollback .bak → target                       updater.py:126
```

Update *checking* (`UpdateService.check_latest`) reads the public GitHub Releases API
(`api.github.com/repos/jrbuda/LeagueCastAssist/releases/latest`), compares `tag_name` against
`__version__`, and selects the release asset ending in `.exe` — preferring the exact name
`LeagueCastAssist.exe` (`update.py:347-381`). Downloads stream to `updates\<tag>.exe.download`, are verified
against `asset.digest` (`sha256:…`, GitHub's newer field) or a sibling `<asset>.sha256` / `SHA256SUMS`
asset, then atomically renamed (`update.py:388-431`).

**Two consequences that matter for packaging:**

1. **The updater only understands a single `.exe` asset.** `_select_windows_exe_asset` filters
   `name.lower().endswith(".exe")`, and `install_update_after_exit` renames one file over another. A folder /
   zip asset is invisible to it today.
2. **`updater.exe` is itself onefile** (`updater.spec`: `runtime_tmpdir=None`, built with `--onefile`), so
   it extracts into `%TEMP%` too. On a machine whose `%TEMP%` is blocked, the *app* fails first — but even
   if it started, the update would fail at the updater stage. Any temp hardening must cover both binaries.

Also worth flagging (pre-existing, not introduced here): the updater assumes the **app directory is
writable**. Installed under `Program Files` without elevation, steps b–c fail and the log records it.

## 4. What this means for "portable"

"Portable install" in this app means "no installer, everything beside the exe" — and that is true for
*data*: `settings.json`, `music.db`, `assets\`, `updates\`, logs. It is **not** true for the *runtime*,
because onefile must unpack Python/Qt/FFmpeg somewhere before any of our code runs. That is unchanged from
every previous release and is not a defect.

## 5. Options

| | Shape | Auto-update | `%TEMP%` needed | Effort | Risk |
|---|---|---|---|---|---|
| **A** | onefile, as today (`%TEMP%`) — **current, no change** | ✅ unchanged | yes | none | none — a genuinely broken `%TEMP%` blocks startup, same as every prior release |
| **B** | onefile + `--runtime-tmpdir %LOCALAPPDATA%\LeagueCastAssist\runtime` | ✅ unchanged | no | ~1 h | unverifiable in this environment (the same cross-volume restriction blocks it) |
| **C** | onedir folder + zip asset + folder-aware updater | ⚠️ needs new updater | no | 2–4 d | medium-high (folder swap vs file locks, rollback, AV) |
| **D** | A/B release + separate portable zip | ⚠️ only for the exe build | no | ~0.5 d | two user-facing download/update behaviours — rejected as needless complexity |

### Why B is not being adopted

`--runtime-tmpdir "%LOCALAPPDATA%\…"` does get baked into the spec and PyInstaller does expand the
environment variable at runtime (verified). But in this environment the bootloader is denied creating
directories on `C:` from an image on `E:`, so B cannot be validated here — and it only relocates the
dependency away from `%TEMP%` rather than removing it. Shipping an unverifiable change to a workspace tool
that several casters rely on is worse than leaving a known-shape release alone.

## 6. Recommendation

**Option A, unchanged.** The release keeps the 0.5.x shape: onefile exe, `.sha256`, bundled `updater.exe`,
in-place auto-update. Nothing to fix, nothing to add.

If a user ever reports the "Could not create temporary directory!" dialog, the diagnosis is their `%TEMP%`
(missing, full, redirected, or blocked by security software), not the app — the fixes are a temporary
`TMP`/`TEMP` override or an AV exclusion. This is recorded in `README.md → Troubleshooting`.

### Option C sketch (only if portable + auto-update are both mandatory)

1. Release publishes `LeagueCastAssist-portable-vX.Y.Z.zip` (folder build) **in addition to** the exe.
2. `update.py`: allow `.zip` assets, download + verify, extract to `updates\<tag>\`, then pass
   `--install-dir --source <dir> --target <app dir>` to a redesigned updater.
3. `updater.exe` must run **outside** the folder it replaces (e.g. `%LOCALAPPDATA%\LeagueCastAssist\updater.exe`),
   otherwise the rename moves the running updater.
4. Swap: `app` → `app.bak`, `source` → `app`, relaunch, delete `.bak`. Roll back on any failure.
5. Failure modes to handle: locked files (AV scan, Explorer window, OneDrive sync), disk space for two
   copies, partial swaps after a crash, and "user launched from the .bak folder".

## 7. Verification performed

- [x] The same `dist\LeagueCastAssist.exe` starts normally when placed on `C:` (`C:\Users\thebu\LcaSmoke`):
      seeds `music.db`, logs `Playback engine ready: 3 audio output(s)`, downloads CDragon data.
- [x] The official `v0.5.5` asset shows the same bootloader error as ours when started from `E:`, so the
      builds are equivalent and nothing regressed.
- [x] Frozen run of the current build logs the multimedia backend, the seeded library, and a successful
      CommunityDragon fetch (see `music-integration-plan.md` §0).
- [ ] Not verified in this environment (needs a normal desktop session): a real Explorer double-click, and a
      live in-place update round-trip (`v0.6.0` → `v0.6.1`) with `updater.log`.

## 8. Follow-ups (not blocking)

* If a user ever reports the temp-dir dialog, ask them to run `LeagueCastAssist.exe` from a normal folder on
  the system drive (e.g. `%USERPROFILE%\Downloads`) before assuming a code problem — that single difference
  is what separated "broken" from "working" in this investigation.
* Code signing would remove a class of AV interference with self-extracting exes; it is the only durable
  mitigation for locked-down corporate machines, and it costs a certificate.
* A folder-based update path (Option C) stays available if self-updating ever needs to work without
  extraction, but it is not worth the complexity today.


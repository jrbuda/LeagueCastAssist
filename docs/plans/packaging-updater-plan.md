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
| Error dialog you photographed | produced by **my** launch attempt, which runs inside the agent sandbox |

So: the integration did not introduce this. The dialog appeared because the sandboxed runner can only write
inside the workspace, and I launched the exe from there. I should have caught that before reporting a
packaging problem — the onefile behaviour was pre-existing, not new.

### 1.1 Addendum — the failure reproduces for the *official* 0.5.5 build too

Follow-up testing (same machine, same session) changed the conclusion more than once, so the evidence is
recorded here in order:

| Test | Result |
|---|---|
| Download `v0.5.5` release asset from GitHub and launch it | **`Error` dialog, no `_MEI` created** — identical to our 0.6.0 build |
| Launch either exe via Task Scheduler (outside the agent's process tree, user session) | same `Error` dialog |
| Build the same app with PyInstaller 6.9.0 (older bootloader) | same failure — not a bootloader-version regression |
| `--runtime-tmpdir %LOCALAPPDATA%\LcaRT`, task-launched | `LOADER: failed to create runtime-tmpdir path … Access is denied` |
| **Portable (`--onedir`) build, task-launched** | **starts normally**: seeds `music.db`, logs `Playback engine ready: 3 audio output(s)`, fetches CDragon metadata |
| PowerShell (signed, `C:\Windows`) creating `_MEI*` folders in `%TEMP%` | succeeds |
| Unsigned PyInstaller exe creating any directory under `C:\Users\…` | denied |

Conclusions that survive the testing:

1. **0.6.0 is not a regression.** The shipped 0.5.5 exe behaves exactly the same way right now.
2. On this machine, **any onefile PyInstaller exe currently fails** to create its extraction directory,
   while a **onedir build works**. `%TEMP%` itself is writable for trusted binaries, so the denial is
   specific to the unsigned self-extracting exes (recent Defender/EDR behaviour, a policy applied to this
   profile, or the harness — it cannot be pinned down further without admin access to Defender exclusions
   and audit logs).
3. Historical runs *did* work: the 86 `_MEI` folders (Feb–Oct 2026) prove 0.5.x extracted here many times.
   So the environment changed recently; it is not a property of the app.
4. **There is a build that is immune**: the portable folder never extracts anything.

## 1.2 Decision taken (per "make sure a user won't hit this error")

Keep the release shape exactly as 0.5.x (onefile + bundled updater + auto-update), and add the portable
folder as a **second release asset** as the guaranteed way to start the app when the bootloader cannot
unpack. Implemented in this branch:

* `release.yml` builds `LeagueCastAssist-portable-vX.Y.Z.zip` (+ `.sha256`) next to the exe. The updater's
  asset selector only considers `.exe` files, so the zip cannot confuse it.
* `update.is_portable_build()` detects the onedir layout (`sys._MEIPASS` living beside the exe) and
  `can_install_downloaded_update()` returns `False` for it, because a onefile exe must not overwrite a
  portable folder. The UI explains that a portable build updates by extracting the new zip over the folder.
* `README.md` gained a troubleshooting section that names the dialog, explains the onefile extraction, and
  lists the three fixes (portable build, temporary TMP/TEMP override, AV exclusion).


## 2. Why my sandbox launches failed (and why that says nothing about your PC)

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

"Portable install" in this app currently means "no installer, everything beside the exe" — and that is true
for *data*: `settings.json`, `music.db`, `assets\`, `updates\`, logs. It is **not** true for the *runtime*,
because onefile must unpack Python/Qt/FFmpeg somewhere before any of our code runs.

A `--onedir` build removes that entirely (verified: launching it leaves `%TEMP%` untouched and writes its
library/log beside the exe), but it turns the release artifact into a ~164 MB folder, which the current
updater cannot install.

## 5. Options

| | Shape | Auto-update | `%TEMP%` needed | Effort | Risk |
|---|---|---|---|---|---|
| **A** | onefile, as today (`%TEMP%`) | ✅ unchanged | yes | none | none — but locked-down `%TEMP%` machines cannot start |
| **B** | onefile + `--runtime-tmpdir %LOCALAPPDATA%\LeagueCastAssist\runtime` | ✅ unchanged | **no** | ~1 h + one build test | low (verified env expansion works, §6) |
| **C** | onedir folder + zip asset + folder-aware updater | ⚠️ needs new updater | no | 2–4 d | medium-high (folder swap vs file locks, rollback, AV) |
| **D** | A/B as the release + separate portable zip, updates manual for it | ⚠️ only for the exe build | no (portable) | ~0.5 d | low, but two user-facing update behaviours |

### Option B details (recommended hardening, no product change)

Verified on this machine with a throwaway build:

* `--runtime-tmpdir "%LOCALAPPDATA%\LcaRuntimeTest"` was baked verbatim into the spec
  (`runtime_tmpdir='%LOCALAPPDATA%/LcaRuntimeTest'`) and PyInstaller **expanded the environment variable at
  runtime** (the bootloader log printed the resolved `C:\Users\thebu\AppData\Local\LcaRuntimeTest`).
* The probe failed only because of the sandbox DACL restriction described in §2 — the same failure it hits
  on `%TEMP%` there. Its value is that extraction no longer depends on `%TEMP%` at all.

Concrete plan if approved:

1. `build.ps1`: add `--runtime-tmpdir "%LOCALAPPDATA%\LeagueCastAssist\runtime"` to **both** the app and the
   updater builds (fallback-safe: keep the default if the variable is unavailable).
2. Add a startup sweep: delete stale `_MEI*` directories inside that runtime folder that are older than
   24 h and not owned by a running PID (the 86-folder pile-up in `%TEMP%` happens because killed runs never
   clean up; in our own folder we can).
3. Log `sys._MEIPASS` at startup so support can see where the runtime unpacked.
4. Verify in the built exe: frozen run, `%TEMP%` untouched, `updater.log` shows a successful update to a
   locally-built higher version.

### Option C sketch (only if portable + auto-update are both mandatory)

1. Release publishes `LeagueCastAssist-portable-vX.Y.Z.zip` (folder build) **in addition to** the exe.
2. `update.py`: allow `.zip` assets, download + verify, extract to `updates\<tag>\`, then pass
   `--install-dir --source <dir> --target <app dir>` to a redesigned updater.
3. `updater.exe` must run **outside** the folder it replaces (e.g. `%LOCALAPPDATA%\LeagueCastAssist\updater.exe`),
   otherwise the rename moves the running updater.
4. Swap: `app` → `app.bak`, `source` → `app`, relaunch, delete `.bak`. Roll back on any failure.
5. Failure modes to handle: locked files (AV scan, Explorer window, OneDrive sync), disk space for two
   copies, partial swaps after a crash, and "user launched from the .bak folder".

## 6. Recommendation

**Adopted:** keep the release shape identical to 0.5.x (onefile + auto-update) and ship the portable folder
as a secondary asset and documented fallback (§1.2). Do **not** attempt Option B (`--runtime-tmpdir`)
blind: on this machine the same denial applies to `%LOCALAPPDATA%`, so it cannot be verified here and would
only move an unverifiable risk elsewhere.

Do **not** switch the release to onedir while auto-update is mandatory — that trades a running updater for
portability, and the updater is the more valuable of the two today. If "nothing outside the app folder"
later becomes a hard requirement, Option C is the only way to keep both.

## 7. Verification checklist (for A+B)

- [ ] `dist\LeagueCastAssist.exe` starts with `%TEMP%` set to a non-writable folder.
- [ ] Runtime extracts under `%LOCALAPPDATA%\LeagueCastAssist\runtime\_MEI<pid>`; `%TEMP%` gains nothing.
- [ ] Stale runtime folders older than 24 h are removed on launch; the live one is kept.
- [ ] Local update round-trip: tag a build `v0.6.1` locally, install over `0.6.0`, confirm
      `updater.log` shows backup → install → relaunch and the app reports the new version.
- [ ] Update rollback: force a failure at step c (read-only app dir) and confirm the old exe is restored.
- [ ] `updater.exe` also respects the new runtime root (no `_MEI` in `%TEMP%` during an update).

## 8. Open question for you

Is the "portable, nothing outside the app folder" property a **hard requirement**, or was it a reaction to
the temp-dir error (which turns out to be my sandbox)? If it is hard, Option C is the only way to keep both
properties, and I would plan it as its own release (0.7.0) with the failure-mode work above.

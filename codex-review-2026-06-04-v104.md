# Codex review, v1.0.4 no-console build (2026-06-04)

Run from the project root: `git diff | codex exec "$(cat <prompt>)"`. The working-tree
diff was piped on stdin. Both the verbatim prompt and Codex's verbatim findings are below.

## Prompt

You are doing an independent, cold code review of UNCOMMITTED changes to Volksmond (a.k.a. SA-Live-Transcribe), a local-only, offline, privacy-first Windows desktop app (Python + FastAPI + pywebview + WebView2, packaged with PyInstaller). You have not seen the conversation that produced these changes. The working-tree diff is on stdin; you may read any file in the repo (read-only).

This is the v1.0.4 change: make the shipped exe windowed so no console or terminal opens on launch.

- sa-live-transcribe.spec: the EXE now builds with console=False (was console=True).
- app_main.py: because a windowed PyInstaller build has no console, sys.stdout and sys.stderr are None, so print() and uncaught tracebacks would raise. A new _redirect_windowed_output() opens a per-launch log file at LOCALAPPDATA/sa-live-transcribe/volksmond.log (truncated each launch) and assigns it to sys.stdout/sys.stderr only when they are None; it falls back to os.devnull on OSError, and is a no-op when a console is present.
- licensing.py: APP_VERSION 1.0.3 to 1.0.4. CHANGELOG.md is doc only.

Review for CORRECTNESS and BUGS, not style. Specifically:
- Can _redirect_windowed_output crash the app on startup in any path (frozen windowed, frozen console, source or dev run)? Is the None-guard correct, and is it safe that the same file object may back both stdout and stderr?
- Does anything in the normal window-mode startup still require a real console or stdin that console=False removes?
- Any way this introduces a network call, telemetry, or writes recorded content to disk outside the existing data dir (it must stay fully offline and private)?
- Is truncating the log each launch acceptable, or is there a correctness risk (for example two instances racing on the same file)?

Answer in under 250 words. Separate BLOCKERS (must fix before commit) from NITS (optional). If nothing is blocking, say so explicitly.

## Findings (verbatim)

**BLOCKERS**
None found.

**NITS**
- app_main.py:31: `_redirect_windowed_output()` is safe in normal frozen windowed, frozen console, and source/dev paths. The guard handles partial `None` correctly, and sharing one file object for stdout/stderr is fine. Tiny edge: `str(Path.home())` is evaluated before `os.environ.get()` returns, so a broken profile env could raise before the `OSError` fallback. Existing app paths use the same pattern, so I would not block on it.
- app_main.py:17: redirect happens after importing `live_transcribe.desktop`. Current import is stdlib-only, so no current startup bug, but moving the import after redirect would capture future import-time failures.
- live_transcribe/desktop.py:154: normal window mode does not require console or stdin. `--server-only`/`--browser` become invisible/no-console modes, but that is not a default-window correctness issue.
- No new network call or telemetry found. The new write is `%LOCALAPPDATA%\sa-live-transcribe\volksmond.log`, inside the existing app data base. Normal web/window startup subscribes `MarkdownSink` and `BrowserSink`, not `StdoutSink`, so transcript text is not newly duplicated into the log.
- Truncating per launch is acceptable for correctness. Two simultaneous instances can clobber/interleave the diagnostic log, but that is support-log quality, not app/data correctness.

## Disposition

- No blockers: clean.
- Acted on the app_main.py:17 nit: moved `from live_transcribe.desktop import main` into
  `__main__` after the redirect, so import-time failures are also captured in the crash log.
- Left the `str(Path.home())` nit: it matches the existing pattern in `licensing.py`; diverging
  would break consistency for a path that effectively never fires on Windows (LOCALAPPDATA is set).
- The `--browser`/`--server-only` no-console behaviour is intended and documented in the CHANGELOG.

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A desktop app that speaks FFXIV dialogue aloud. It connects as a WebSocket *client* to the
Dalamud/XIVLauncher TTS plugin at `ws://localhost:51363/Messages`, receives `Say`/`Cancel`
messages, and synthesizes speech locally with [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M).
Everything ships as one executable with a customtkinter GUI; `--headless` runs the same runtime
on the console.

## Commands

```bash
python src/main.py              # run from source (fastest iteration loop)
python src/main.py --headless   # no GUI
./build.sh                      # PyInstaller build into dist/ (build.bat on Windows)
```

Requires **Python 3.12** — the dependency chain (torch/kokoro/misaki) doesn't support newer.
On Arch, create the env with `uv venv --python 3.12 .venv`; `build.sh` handles uv venvs that
have no `pip` binary. System packages needed: `portaudio libsndfile alsa-lib` (plus
`pipewire-alsa` so PipeWire devices show up).

`requirements.txt` points at PyTorch's CPU wheel index. Keep installs going through that file —
installing torch bare pulls the multi-gigabyte CUDA build, which this app never uses.

`build.sh` refreshes `dist/lexicons` on every build but keeps a name saved there on the Name
screen. It doesn't copy `settings/` at all: the binary merges its built-in defaults into
`dist/settings` when it starts, so your configured settings survive a rebuild; delete that
folder to get the defaults back.

There is no test suite and no linter configured. Verify changes by running the app.

## Architecture

`src/` is flat and imported as top-level modules (`import tts`, not `src.tts`) because
PyInstaller's `pathex=['src']` puts that directory on the path.

- **`main.py`** — entry point. `_ensure_streams()` matters: a windowed PyInstaller build has
  `sys.stdout is None`, so any `print()` inside torch/kokoro would crash the app. The Windows
  exe is windowed too, so `--headless` first attaches a console (`_attach_console()`), or
  nothing it prints is visible. `defaults.install()` runs before anything reads `settings/`.
- **`tts.py`** — `TTSService` wraps the websocket listener on a daemon thread with
  start/stop/reconnect. `stop()` only signals: `running` stays True until the worker has really
  exited, and that is what stops `start()` from running a second worker beside one still
  loading the model, speaking a line or waiting to reconnect — don't clear it in `stop()`.
  Kokoro is imported *inside* `_load()`, not at module scope, so the window appears before
  torch's multi-second import. Each line is synthesized whole, then played with a single
  `sd.play()`: `sd.play()` stops whatever is still playing, so playing chunk by chunk cut long
  lines off. Playback volume and speed come from the `settings["voice_settings"]` override for
  the chosen voice where it has one; otherwise volume falls back to the per-*gender* default
  keyed on the voice's own gender (`voice_data.classify_gender(voice)`, where `voice_data` is
  `voices.py`) and speed to the global `speed`, each key separately. The gender is the voice's,
  not the plugin's reported `Voice.Name` — that field is only the fallback for characters
  absent from `voices.json`. Comparing against `female_voice` instead was a real bug: it
  matched only the one regional default, so every other female voice played at the male volume.
- **`settings_gui.py`** — sidebar of `Screen` subclasses (Run/General/Tuning/Name/Voices/Audio),
  registered in `SettingsApp.SCREENS`. Screens are built once and kept; `SettingsApp.show()`
  calls the incoming screen's `on_show()`, which is how Tuning picks up defaults General saved.
  `SettingsApp` tees both `sys.stdout` and `sys.stderr` through `LogStream` into the Run
  screen's log, after building every screen. Tk isn't thread-safe: other threads hand log
  lines and the update-check result to `RunScreen` through queues that its `drain()` loop
  applies every 150 ms — a cross-thread `self.after()` raises if it lands before `mainloop()`
  runs. (`on_service_state` still uses `after(0, ...)`; the worker only exists once the loop
  is running.) Each screen writes only its own keys via `update_settings(**changes)`, which
  re-reads the file first so screens don't clobber each other. `DEFAULT_SETTINGS` is only the
  GUI's fallback for keys missing from the file: new installs get their actual defaults from
  the shipped `settings/settings.json` (speed 1.3, not 1.0), and `tts._load()` reads the file
  itself with its own `.get()` fallbacks, so a new runtime setting needs a fallback there too.
  Tuning overrides store only the values that differ from the General defaults.
- **`voices.py`** — shared character→voice data plus Kokoro's published quality grades. The
  picker sorts females first, then by grade, since every male voice is C+ or below.
  `classify_gender()` reads the id's second letter (Kokoro ids are `<lang><gender>_<name>`).
- **`audio.py`** — PortAudio device selection. Deliberately prefers a `pipewire`/`pulse` device
  over ALSA's `default` alias, which doesn't follow the user's desktop audio setting. Two other
  workarounds live here: saved device names are matched with the `(hw:X,Y)` suffix stripped
  (those indices shift between boots and when a wireless headset sleeps), and
  `refresh_devices()` tears down and re-initialises PortAudio because it caches the device list
  at init, hiding anything switched on after launch.
- **`updates.py`** / **`version.py`** — update check against the GitHub Releases API. It only
  *detects* a newer release and links to the download page; it never replaces the binary. TLS
  is verified against the system store plus certifi's bundle, because the Linux build's
  bundled OpenSSL looks for CA certificates in Ubuntu's `/usr/lib/ssl`, which Arch and Fedora
  don't have. `check()` never raises: any failure (offline, rate-limited, TLS) returns `None`
  silently, so a broken check looks exactly like "no update". `check_async()` calls back on a
  worker thread. `version.py` holds `"0.0.0-dev"` in the repo; `_parse()` fullmatches
  `v?X.Y.Z`, so the `-dev` suffix makes source runs and local builds skip the check before any
  network call. Leave that default committed — CI overwrites the file at build time.
- **`defaults.py`** — merges the settings bundled into a frozen build into the user's
  `settings/` at every start: a missing file is copied, a missing settings key added, and a
  character new to the shipped `voices.json` added with its shipped voice; anything the user
  already has keeps their value. `settings/.shipped-voices.json` keeps the shipped list from
  the last merge, so characters removed on the Voices screen stay removed. A no-op from source.
- **`paths.py`** — resolves `settings/` and `lexicons/` next to the executable when frozen,
  next to the repo root otherwise; `defaults_dir()` is the bundled defaults inside a frozen
  build.

### Data files

The app writes to both `settings/` and `lexicons/` at runtime, so they live as **editable
folders beside the executable**. The spec bundles only read-only copies of
`settings/settings.json` and `settings/voices.json` (for `defaults.py`). Release zips hold the
binary plus `lexicons/` without `Your-Name/lexicon.pls`, so unzipping an update over an install
never touches user state; build.sh/build.bat/CI copy `lexicons/` into `dist/` that way.

- `settings/settings.json` — region (US/UK picks the Kokoro `lang_code` and default voices),
  speed, per-gender volume, audio device, and `voice_settings` (per-voice overrides holding
  `volume` and/or `speed`; whatever's missing falls back to the gender/global defaults).
- `settings/voices.json` — `{expansion: {character: voice_id}}`, flattened to a lowercased
  lookup at runtime. `voices.py` still converts the legacy `{gender: {voice: [names]}}` layout.
- `lexicons/*/lexicon.pls` — W3C PLS pronunciation files, vendored from
  [TextToTalk](https://github.com/karashiiro/TextToTalk/tree/main/lexicons). `<phoneme>` entries
  are pushed into `pipeline.g2p.lexicon.golds`; `<alias>` entries become plain word
  substitutions applied before synthesis. The Name screen rewrites `Your-Name/lexicon.pls`
  wholesale; it's the user's file, so builds leave it out. `LEXICON_DIRECTORIES` is a **whitelist, not a
  directory scan** — `lexicons/` also holds packages (`Characters-Locations-Polly`,
  `Yugiri-Western-Pronunciation`) that deliberately contradict `Characters-Locations-System`,
  so they stay unlisted and inert. Merge order is the tuple's order and a later entry wins
  collisions. Every file is currently byte-identical to upstream, so re-syncing is a plain
  copy; `lexicons/README.md` documents each package and the Polly-specific notation that
  misaki can't parse.

### Packaging gotchas (`ffxiv-tts.spec`)

Non-obvious constraints, the first two already commented in the spec — preserve them:

1. `libasound`/`libpulse`/`libpulsecommon`/`libjack` are stripped from the bundle so the host's
   copies are used. A bundled libasound looks for ALSA plugins in the *build* machine's paths,
   which makes the `pipewire`/`pulse` devices vanish on the user's machine. `libpulsecommon`
   must come from the same place as `libpulse`: when the runner's and the host's share a
   version name, a bundled copy shadows the host's and libpulse fails to load (seen when
   `ubuntu-latest` became 26.04).
2. kokoro's G2P chain (`misaki`, `phonemizer`, `segments`, `csvw`, `language_tags`,
   `espeakng_loader`) and customtkinter read data files by path, so they need `collect_all`.
   `en_core_web_sm` isn't a pip dependency — every build path runs
   `python -m spacy download en_core_web_sm` first.
3. A frozen Linux build runs with `LD_LIBRARY_PATH` pointing into its bundle, and child
   processes inherit it, so a launched `/bin/sh` or browser loads the CI runner's libraries and
   can crash. Restore `LD_LIBRARY_PATH_ORIG` around anything the app launches, as
   `settings_gui.open_in_browser()` does.

## Releases

Every push to `main` builds Linux + Windows on GitHub runners, auto-tags the next patch version,
stamps it into `src/version.py`, and publishes a release. Runs share one concurrency group, so a
push during a build (~9 minutes) waits instead of computing the same version, and the tag is
created at the commit that was built (`target_commitish`), not at `main`'s tip. Pushes that only
change `*.md` or `LICENSE` don't build. `[skip ci]` skips a whole push, but GitHub reads it only
from the newest commit, so it also skips code commits pushed along with it — never put it on a
docs commit pushed on top of code. Minor/major bumps are the one manual step: push the tag
yourself first (`git tag v0.2.0 && git push origin v0.2.0`) and patch numbering continues from
there. Manual `workflow_dispatch` runs build artifacts without releasing, stamped `<tag>-test`
so they keep the update check off — use that to test a build. upload-artifact drops file
permissions, so the release job restores the Linux binary's execute bit before zipping.
PyInstaller can't cross-compile, so the Windows `.exe` can only come from CI or a Windows
machine.

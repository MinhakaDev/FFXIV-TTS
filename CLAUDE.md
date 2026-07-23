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

`build.sh` replaces `dist/lexicons` on every build but **keeps** an existing `dist/settings`, so
your configured settings survive a rebuild; delete that folder to get the defaults back.

There is no test suite and no linter configured. Verify changes by running the app.

## Architecture

`src/` is flat and imported as top-level modules (`import tts`, not `src.tts`) because
PyInstaller's `pathex=['src']` puts that directory on the path.

- **`main.py`** — entry point. `_ensure_streams()` matters: a windowed PyInstaller build has
  `sys.stdout is None`, so any `print()` inside torch/kokoro would crash the app.
- **`tts.py`** — `TTSService` wraps the websocket listener on a daemon thread with
  start/stop/reconnect. Kokoro is imported *inside* `_load()`, not at module scope, so the
  window appears before torch's multi-second import. Playback volume and speed come from the
  `settings["voice_settings"]` override for the chosen voice if it has one (set on the GUI's
  Voice Tuning screen); otherwise volume falls back to the per-*gender* default keyed on the
  voice's own gender (`voice_data.classify_gender(voice)`) and speed to the global `speed`. The
  gender is the voice's, not the plugin's reported `Voice.Name` — that field is only the
  fallback for characters absent from `voices.json`. Comparing against `female_voice` instead
  was a real bug: it matched only the one regional default, so every other female voice played
  at the male volume.
- **`settings_gui.py`** — sidebar of `Screen` subclasses (Run/General/Name/Voices/Audio),
  registered in `SettingsApp.SCREENS`. The Run screen tees `sys.stdout` through `LogStream`
  into an on-screen log. Each screen writes only its own keys via `update_settings(**changes)`,
  which re-reads the file first so screens don't clobber each other. `DEFAULT_SETTINGS` is the
  source of truth for which settings keys exist and their defaults — the shipped
  `settings.json` can lag it (it has no `appearance` key, for one), and `load_settings()` layers
  the file over those defaults.
- **`voices.py`** — shared character→voice data plus Kokoro's published quality grades. The
  picker sorts females first, then by grade, since every male voice is C+ or below.
- **`audio.py`** — PortAudio device selection. Deliberately prefers a `pipewire`/`pulse` device
  over ALSA's `default` alias, which doesn't follow the user's desktop audio setting. Two other
  workarounds live here: saved device names are matched with the `(hw:X,Y)` suffix stripped
  (those indices shift between boots and when a wireless headset sleeps), and
  `refresh_devices()` tears down and re-initialises PortAudio because it caches the device list
  at init, hiding anything switched on after launch.
- **`paths.py`** — resolves `settings/` and `lexicons/` next to the executable when frozen,
  next to the repo root otherwise.

### Data files (not bundled into the binary)

`settings/` and `lexicons/` ship as **editable folders beside the executable** — the app writes
to both at runtime. `ffxiv-tts.spec` intentionally excludes them; `build.sh`/`build.bat`/CI copy
them into `dist/` afterwards.

- `settings/settings.json` — region (US/UK picks the Kokoro `lang_code` and default voices),
  speed, per-gender volume, audio device, and `voice_settings` (per-voice `{volume, speed}`
  overrides, absent voices fall back to the gender/global defaults).
- `settings/voices.json` — `{expansion: {character: voice_id}}`, flattened to a lowercased
  lookup at runtime. `voices.py` still converts the legacy `{gender: {voice: [names]}}` layout.
- `lexicons/*/lexicon.pls` — W3C PLS pronunciation files, vendored from
  [TextToTalk](https://github.com/karashiiro/TextToTalk/tree/main/lexicons). `<phoneme>` entries
  are pushed into `pipeline.g2p.lexicon.golds`; `<alias>` entries become plain word
  substitutions applied before synthesis. The Name screen rewrites `Your-Name/lexicon.pls`
  wholesale. `LEXICON_DIRECTORIES` is a **whitelist, not a directory scan** — `lexicons/` also
  holds packages (`Characters-Locations-Polly`, `Yugiri-Western-Pronunciation`) that
  deliberately contradict `Characters-Locations-System`, so they stay unlisted and inert.
  Merge order is the tuple's order and a later entry wins collisions. Every file is currently
  byte-identical to upstream, so re-syncing is a plain copy; `lexicons/README.md` documents each
  package and the Polly-specific notation that misaki can't parse.

### Packaging gotchas (`ffxiv-tts.spec`)

Two non-obvious constraints, both already commented in the spec — preserve them:

1. `libasound`/`libpulse`/`libjack` are stripped from the bundle so the host's copies are used.
   A bundled libasound looks for ALSA plugins in the *build* machine's paths, which makes the
   `pipewire`/`pulse` devices vanish on the user's machine.
2. kokoro's G2P chain (`misaki`, `phonemizer`, `segments`, `csvw`, `language_tags`,
   `espeakng_loader`) and customtkinter read data files by path, so they need `collect_all`.
   `en_core_web_sm` isn't a pip dependency — every build path runs
   `python -m spacy download en_core_web_sm` first.

## Releases

Every push to `main` builds Linux + Windows on GitHub runners, auto-tags the next patch version,
and publishes a release. `[skip ci]` in the commit message skips it. Minor/major bumps are the
one manual step: push the tag yourself first (`git tag v0.2.0 && git push origin v0.2.0`) and
patch numbering continues from there. Manual `workflow_dispatch` runs build artifacts without
releasing — use that to test a build. PyInstaller can't cross-compile, so the Windows `.exe`
can only come from CI or a Windows machine.

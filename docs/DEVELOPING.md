# Developing FFXIV TTS

Players don't need any of this: they download a release (see the [README](../README.md)).
This page is for running the app from source, building it, and publishing releases.

## Run from source

The dependencies need **Python 3.12** (torch, kokoro and misaki don't support newer ones).

1. Install Python 3.12. If your system Python is newer (Arch ships 3.14), make a 3.12
   environment with [uv](https://docs.astral.sh/uv/):
   ```bash
   uv python install 3.12
   uv venv --python 3.12 .venv
   source .venv/bin/activate
   ```
2. Install the dependencies. `requirements.txt` points at PyTorch's CPU wheels, so keep
   installing through it: plain `pip install torch` pulls the multi-gigabyte CUDA build,
   which the app never uses.
   ```bash
   pip install -r requirements.txt
   python -m spacy download en_core_web_sm
   ```
   On Linux, install the audio libraries first: `portaudio libsndfile alsa-lib` (Arch), or
   `libportaudio2 libsndfile1` (Debian, Ubuntu). Add `pipewire-alsa` so PipeWire devices show up.
3. Start XIVLauncher with TextToTalk set up (see the README), then:
   ```bash
   python src/main.py              # the window
   python src/main.py --headless   # no window: the TTS runs in the terminal
   ```

`--headless` works in a release too: `ffxiv-tts --headless`, or `ffxiv-tts.exe --headless`
on Windows. There, the prompt comes back straight away, but the output keeps appearing in that
terminal; press Ctrl+C in it to stop.

## Build locally

Building locally is a faster feedback loop for packaging changes. It isn't how releases are
made, and it can't produce the Windows `.exe`: PyInstaller doesn't cross-compile, so build on
the OS you're targeting (`build.sh` on Linux, `build.bat` on Windows).

```bash
source .venv/bin/activate
./build.sh
./dist/ffxiv-tts
```

`build.sh` works with both plain and uv-made virtualenvs, refreshes `dist/lexicons` (keeping a
name you saved on the Name screen) and keeps any settings you configured in `dist/`. For
changes that aren't about packaging, `python src/main.py` skips PyInstaller entirely.

## Releases

Every push to `main` builds the Windows `.exe` and the Linux binary on GitHub's runners, tags
the commit with the next patch version, and publishes a GitHub Release with both zips. There's
nothing to run and no tags to make by hand: see
[`.github/workflows/build.yml`](../.github/workflows/build.yml).

- Pushes that only change Markdown files, `docs/` or `LICENSE` don't build, so docs edits need
  nothing special.
- `[skip ci]` in a commit message skips a build, but GitHub only reads it from the newest
  commit of a push, so it also skips any code commits pushed along with it. Push those
  separately, or leave the marker off.
- Bumping the minor or major version is the one manual step: push that tag yourself
  (`git tag v0.3.0 && git push origin v0.3.0`) and later pushes continue from it.
- Running the workflow by hand from the Actions tab builds downloadable artifacts **without**
  publishing a release, which is useful for testing a build.

### The Qwen3-TTS library

The Qwen3 engine downloads a compiled library on its first Start, from a GitHub
**pre-release** tagged `qwen3-runtime-<first 7 characters of QWENTTS_COMMIT>` (see
`src/qwen_engine.py`). Pushing that tag, from any branch, runs
[`.github/workflows/qwen3-runtime.yml`](../.github/workflows/qwen3-runtime.yml), which builds
it for Linux and Windows and publishes it. It must stay a pre-release: the app's update check
reads the latest *release*, which skips pre-releases.

```bash
git tag qwen3-runtime-de2ad83 && git push origin qwen3-runtime-de2ad83
```

`tools/setup_qwen3.sh` builds the library locally instead, and `tools/design_voices.py` makes
the voices in `qwen3-voices/` from the descriptions in `qwen3-voices/designs.json`.

## Audio on Linux

ALSA is deliberately not bundled, so the app uses your system's copy and can see the
`pipewire`/`pulse` devices, which are ALSA plugins. If those don't appear in the Audio screen's
device list, install the ALSA bridge for your sound server: `pipewire-alsa` (or
`pulseaudio-alsa`) on Arch, `pipewire-alsa` or `libasound2-plugins` on Debian and Ubuntu.

Picking a raw `(hw:X,Y)` device is fragile: those vanish when a wireless headset sleeps, and
their numbering shifts between reboots. `auto` follows the system's output instead.

## More

[CLAUDE.md](../CLAUDE.md) describes the architecture, the packaging pitfalls and why things
are the way they are, module by module.

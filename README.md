# FFXIV Launcher with TTS Setup Guide

This guide will help you set up the FFXIV Launcher with TTS (Text-to-Speech) and [kokoro-tts](https://github.com/nazdridoy/kokoro-tts) functionality.

---

## Why I Created This Project
The purpose of this project is to enhance the FFXIV experience by integrating good quality Text-to-Speech (TTS) capabilities. Whether for accessibility, immersion, or just for fun, this setup makes the game more interactive and customizable.

---

## Installation Guide

If you don't already have the FFXIV launcher installed and configured, follow the instructions in
[How to Install and Configure FFXIV Launcher](#how-to-install-and-configure-ffxiv-launcher) first.

### Option A: Download a release (recommended, no Python required)

1. Download the latest release zip for your platform (`ffxiv-tts.exe` on Windows, `ffxiv-tts` on
   Linux), which includes `ffxiv-tts-settings`, a `settings/` folder, and a `lexicons/` folder.
2. Run `ffxiv-tts-settings` once to configure your region, speed, volumes, name pronunciation,
   and character voice assignments (see [Settings](#settings)).
3. Open FFXIV with the launcher and log in.
4. Run `ffxiv-tts`.

On Linux you'll also need the system audio libraries the binary links against:
`sudo apt-get install portaudio19-dev libsndfile1` (or your distro's equivalent).

### Option B: Run from source

1. Download [Python 3.12](https://www.python.org/downloads/release/python-3120/).
2. Download this GitHub repository.
3. `pip install -r requirements.txt` (Linux also needs
   `sudo apt-get install portaudio19-dev libsndfile1` first).
4. `python src/settings_gui.py` to configure your settings (see [Settings](#settings)).
5. Open FFXIV with the launcher and log in, then `python src/main.py`.

---

### You're All Set!
Congratulations! Your setup is complete, and you can now start listening instead of reading.

---
## Settings
`ffxiv-tts-settings` (or `python src/settings_gui.py` when running from source) opens a small GUI
where you can customize:
- [x] The Pronunciation of Your Name.
- [x] If you want British English or American English
- [x] The speed at which the male and female voices speak
- [x] The Volume of the TTS
- [x] Which voice each character/NPC speaks with
- [ ] Use GPU

---

## Building the executables

Building the executables is only needed if you're packaging a new release; most users should just
use Option A above.

**Automatically (recommended):** push a version tag (`git tag v1.0.0 && git push --tags`) and
[`.github/workflows/build.yml`](.github/workflows/build.yml) builds both the Windows `.exe` and
the Linux binary on GitHub's own runners and attaches them to a GitHub Release. You can also
trigger it manually without tagging from the repo's Actions tab ("Build binaries" -> "Run
workflow") to get downloadable build artifacts.

**Manually:**

```
./build.sh      # Linux, produces dist/ffxiv-tts, dist/ffxiv-tts-settings
build.bat       # Windows, produces dist\ffxiv-tts.exe, dist\ffxiv-tts-settings.exe
```

PyInstaller doesn't cross-compile, so build on the OS you're targeting: `build.sh` on Linux for
the Linux binary, `build.bat` on Windows for the `.exe`. Each run installs `requirements.txt` +
`requirements-build.txt` and copies `settings/`/`lexicons/` into `dist/` so the folder is ready to
zip and share.

---

## How to Install and Configure FFXIV Launcher
1. Download the [FFXIV Launcher](https://goatcorp.github.io) from the official website
3. Launch the FFXIV Launcher and log in with your account credentials.
4. Open the settings and configure the launcher as needed:
   - Add TTS functionality.
   - Change Voice to WebSocket
   - In TTS Settings Change the port to **51363**.
   - Adjust player references in the settings.
   - You can change what you hear in the setting (NPC, Party, Says).

Return to the [Installation Guide](#installation-guide) once the FFXIV Launcher is installed and configured.
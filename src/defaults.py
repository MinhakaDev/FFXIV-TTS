"""Brings the user's settings up to date with the defaults shipped in the binary.

Release zips carry no settings/ folder, so unzipping an update over an install can't
overwrite what the user configured. Instead a frozen build carries the shipped
settings.json and voices.json inside itself and merges them in each time it starts:

- a file the user doesn't have yet is copied whole;
- a settings key missing from the user's file is added with its shipped value;
- a character new to the shipped voices.json is added with its shipped voice.

Everything the user already has keeps their value, including the voice of a character
in both files. Running from source skips all of this: the repo's settings/ already is
the shipped copy.
"""

import filecmp
import json
import os
import shutil
import sys

import paths
import voices as voice_data

# The shipped voices.json as of the last merge, kept beside the user's. A character
# that was shipped then but is missing from the user's file was removed on the Voices
# screen, so it stays removed; only names new since then are added.
SHIPPED_VOICES = ".shipped-voices.json"


def install():
    if not getattr(sys, "frozen", False):
        return
    os.makedirs(paths.settings_dir(), exist_ok=True)
    for name, merge in (("settings.json", _merge_settings), ("voices.json", _merge_voices)):
        try:
            merge(os.path.join(paths.defaults_dir(), name), os.path.join(paths.settings_dir(), name))
        except Exception as exc:
            # Never block startup over this: the app still runs on what's already there.
            print(f"Could not merge the shipped {name} into your settings: {exc}")


def _merge_settings(shipped_path, user_path):
    if not os.path.exists(user_path):
        shutil.copyfile(shipped_path, user_path)
        return
    with open(shipped_path, "r", encoding="utf-8") as f:
        shipped = json.load(f)
    with open(user_path, "r", encoding="utf-8") as f:
        settings = json.load(f)
    missing = {key: value for key, value in shipped.items() if key not in settings}
    if missing:
        settings.update(missing)
        with open(user_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4)


def _merge_voices(shipped_path, user_path):
    seen_path = os.path.join(os.path.dirname(user_path), SHIPPED_VOICES)
    if not os.path.exists(user_path):
        shutil.copyfile(shipped_path, user_path)
    else:
        user = voice_data.load(user_path)
        have = set(voice_data.build_lookup(user))
        seen = set(voice_data.build_lookup(voice_data.load(seen_path))) if os.path.exists(seen_path) else set()
        added = 0
        for expansion, characters in voice_data.load(shipped_path).items():
            for person, voice in characters.items():
                if person.lower() in have or person.lower() in seen:
                    continue
                user.setdefault(expansion, {})[person] = voice
                have.add(person.lower())
                added += 1
        if added:
            voice_data.save(user_path, user)
            print(f"Added {added} character voice{'s' if added != 1 else ''} from this release")
    if not os.path.exists(seen_path) or not filecmp.cmp(shipped_path, seen_path, shallow=False):
        shutil.copyfile(shipped_path, seen_path)

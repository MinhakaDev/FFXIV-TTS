"""Character-to-voice data, shared by the TTS runtime and the settings GUI."""

import json

# Overall grades published in Kokoro's VOICES.md. They estimate the quality and
# quantity of each voice's training data, so a higher grade sounds noticeably better.
# Note there is no S or B+ tier - A is the top, and every male voice is C+ or below.
# https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md
VOICE_GRADES = {
    # American English - female
    "af_heart": "A",
    "af_bella": "A-",
    "af_nicole": "B-",
    "af_aoede": "C+",
    "af_kore": "C+",
    "af_sarah": "C+",
    "af_alloy": "C",
    "af_nova": "C",
    "af_sky": "C-",
    "af_jessica": "D",
    "af_river": "D",
    # American English - male
    "am_fenrir": "C+",
    "am_michael": "C+",
    "am_puck": "C+",
    "am_echo": "D",
    "am_eric": "D",
    "am_liam": "D",
    "am_onyx": "D",
    "am_santa": "D-",
    "am_adam": "F+",
    # British English - female
    "bf_emma": "B-",
    "bf_isabella": "C",
    "bf_alice": "D",
    "bf_lily": "D",
    # British English - male
    "bm_fable": "C",
    "bm_george": "C",
    "bm_lewis": "D+",
    "bm_daniel": "D",
    # Mandarin, not graded in VOICES.md
    "zm_yunjian": "-",
}

GRADE_ORDER = ["A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-", "F+", "F", "-"]

# Ordered so the settings GUI lists expansions chronologically. "Custom" collects
# characters the user adds themselves.
EXPANSIONS = [
    "A Realm Reborn",
    "Heavensward",
    "Stormblood",
    "Shadowbringers",
    "Endwalker",
    "Dawntrail",
    "Custom",
]


def classify_gender(voice):
    return "female" if voice.startswith(("af_", "bf_", "zf_")) else "male"


def grade_of(voice):
    return VOICE_GRADES.get(voice, "-")


def grade_rank(voice):
    grade = grade_of(voice)
    return GRADE_ORDER.index(grade) if grade in GRADE_ORDER else len(GRADE_ORDER)


def voice_options():
    """Voice ids for the picker: females first, then males, best grade first."""
    return sorted(
        VOICE_GRADES,
        key=lambda v: (classify_gender(v) != "female", grade_rank(v), v),
    )


def describe_voice(voice):
    """Label shown in the picker, e.g. 'af_heart - A (female)'."""
    return f"{voice} - {grade_of(voice)} ({classify_gender(voice)})"


def voice_from_description(text):
    """Recover the voice id from a picker label, tolerating a hand-typed id."""
    return text.split(" - ", 1)[0].strip()


def _is_legacy(data):
    """True for the old {gender: {voice: [people]}} layout."""
    for group in data.values():
        for value in group.values():
            return isinstance(value, list)
    return False


def _from_legacy(data):
    """Convert the old gender-keyed layout, putting everyone under 'Custom'.

    Only reached for a voices.json written before expansions existed; the shipped
    file is already in the current format.
    """
    converted = {}
    for group in data.values():
        for voice, people in group.items():
            for person in people:
                converted[person] = voice
    return {"Custom": converted} if converted else {}


def load(path):
    """Read voices.json as {expansion: {person: voice}}."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _from_legacy(data) if _is_legacy(data) else data


def save(path, data):
    ordered = {
        expansion: dict(sorted(characters.items()))
        for expansion in sorted(data, key=lambda e: (EXPANSIONS.index(e) if e in EXPANSIONS else len(EXPANSIONS), e))
        for characters in [data[expansion]]
        if characters
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ordered, f, indent=2, ensure_ascii=False)
        f.write("\n")


def build_lookup(data):
    """Flatten to {lowercased character name: voice} for runtime lookups."""
    return {
        person.lower(): voice
        for characters in data.values()
        for person, voice in characters.items()
    }

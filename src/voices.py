"""Character-to-voice data, shared by the TTS runtime and the settings GUI."""

import json
import zlib

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

# What a named character missing from voices.json speaks with under Kokoro, picked by
# voice_by_name(). The voices graded C or better, listed rather than worked out from
# VOICE_GRADES: adding or removing one moves most characters to another voice.
NAMED_VOICES = {
    "female": (
        "af_heart", "af_bella", "af_nicole", "af_aoede", "af_kore",
        "af_sarah", "af_alloy", "af_nova", "bf_emma", "bf_isabella",
    ),
    "male": ("am_fenrir", "am_michael", "am_puck", "bm_fable", "bm_george"),
}

# The races the plugin reports, as the game's Race sheet names them in English, and the
# ages (its BodyType) that get voices of their own whatever the race.
RACES = ("hyur", "elezen", "lalafell", "miqo'te", "roegadyn", "au ra", "hrothgar", "viera")
AGES = ("youth", "elder")

# Like NAMED_VOICES, for a character the game says the race of: race_of() picks the
# list, voice_by_name() the voice in it. Kokoro can't change a voice's pitch, so these
# go by manner more than size, and lists share voices: there are only five good male ones.
RACE_VOICES = {
    "hyur": {"female": ("af_heart", "af_sarah"), "male": ("am_michael", "am_puck")},
    "elezen": {"female": ("bf_emma", "bf_isabella"), "male": ("bm_fable", "bm_george")},
    "lalafell": {"female": ("af_bella", "af_sky"), "male": ("am_puck", "bm_fable")},
    "miqo'te": {"female": ("af_aoede", "af_nova"), "male": ("am_puck", "bm_fable")},
    "roegadyn": {"female": ("af_kore", "af_alloy"), "male": ("am_fenrir", "am_michael")},
    "au ra": {"female": ("af_nicole", "af_kore"), "male": ("bm_fable", "am_michael")},
    "hrothgar": {"female": ("af_kore", "af_alloy"), "male": ("am_fenrir", "bm_george")},
    "viera": {"female": ("af_aoede", "bf_isabella"), "male": ("bm_fable", "am_michael")},
    "youth": {"female": ("af_sky", "af_bella"), "male": ("am_puck",)},
    "elder": {"female": ("af_kore", "bf_isabella"), "male": ("bm_george", "am_michael")},
}


def race_of(race, body_type):
    """Whose voices a speaker gets, from the plugin's Race and BodyType: "youth" or
    "elder" for a child or an old one, else their race. "" when the game didn't say,
    as for a speaker who isn't on screen or isn't one of the playable races."""
    race = (race or "").strip().lower().replace("’", "'")
    if race not in RACES:
        return ""
    age = (body_type or "").lower()
    return age if age in AGES else race

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
    # Kokoro ids are <language><gender>_<name> (af_heart, bm_fable, jf_alpha, ff_siwis),
    # so the second letter says it for every language, not just the ones listed above.
    return "female" if voice[1:3] == "f_" else "male"


def voice_by_name(name, voices):
    """One of `voices` for a character, the same one every time for their name.
    crc32, not hash(): Python salts string hashes per run, so hash() picked another
    voice after every restart."""
    return voices[zlib.crc32(name.lower().encode("utf-8")) % len(voices)]


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

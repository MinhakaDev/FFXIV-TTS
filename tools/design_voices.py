"""Makes the Qwen3-TTS voices: one clip per entry in qwen3-voices/designs.json, spoken by
a voice the VoiceDesign model invents from that entry's description, so it belongs to no
real person. The app's Qwen3 engine clones these clips (see src/qwen_engine.py), which
ship with it from qwen3-voices/, so commit them once they sound right. The app's Voices
screen, under Qwen3, does the same one voice at a time, letting you listen before keeping one.

    python tools/design_voices.py                 every voice that has no clip yet
    python tools/design_voices.py thancred cid    just these, replacing their clips

The first run downloads the VoiceDesign model (2 GB) into qwen3-tts/. The library comes
from tools/setup_qwen3.sh, or is downloaded once the qwen3-runtime workflow published it.

An entry is a description, or {"instruct": description, "seed": n}: the seed picks
which voice the description gets (1 when absent), so change it to try another one.
The Voices screen, under Qwen3, stores the seed of each voice it keeps.
Entries named after a character are that character's voice. A character without one gets
one of their race's voices ("lalafell female 1"...), or one of those named after Kokoro
voices when the game doesn't say their race; "default female" and "default male" speak
lines with neither a name nor a race.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import qwen_engine  # noqa: E402


def main(names):
    data = qwen_engine.load_designs()
    text, designs = data["reference_text"], data["voices"]

    unknown = [name for name in names if name not in designs]
    if unknown:
        sys.exit(f"Not in {qwen_engine.designs_path()}: {', '.join(unknown)}")
    todo = names or [
        name for name in designs
        if not os.path.exists(os.path.join(qwen_engine.voices_dir(), f"{name}.wav"))
    ]
    if not todo:
        print("Every voice already has a clip. Name voices to make them again.")
        return

    designer = qwen_engine.Designer()
    try:
        for name in todo:
            design = designs[name]
            started = time.monotonic()
            samples = designer.design(design["instruct"], text, design["seed"])
            qwen_engine.write_clip(name, samples, text)
            print(f"{name}: {len(samples) / qwen_engine.SAMPLE_RATE:.1f} s of audio "
                  f"in {time.monotonic() - started:.1f} s")
    finally:
        designer.close()
    print(f"Clips are in {qwen_engine.voices_dir()}")


if __name__ == "__main__":
    main([name.lower() for name in sys.argv[1:]])

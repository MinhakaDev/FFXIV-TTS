"""The TTS runtime: listens to the FFXIV launcher and speaks dialogue.

Wrapped in a service so the app can start and stop it without restarting, and so
loading Kokoro (slow, pulls in torch) happens on a worker thread rather than at
import time.
"""

import json
import os
import threading
import time
import xml.etree.ElementTree as ET

import numpy as np
import sounddevice as sd
import websocket

import audio as audio_output
import paths
import voices as voice_data
from audio import KOKORO_SAMPLE_RATE

WEBSOCKET_URL = "ws://localhost:51363/Messages"
PLS_NAMESPACE = "{http://www.w3.org/2005/01/pronunciation-lexicon}"
LEXICON_DIRECTORIES = (
    "Characters-Locations-System",
    "Your-Name",
    "Stutter-Replacers",
    "Chat-FFXIV-Acronyms",
)
REGION_VOICES = {
    "US": ("a", "am_puck", "af_heart"),
    "UK": ("b", "bm_fable", "bf_emma"),
}


def parse_pls(filename, aliases):
    """Read one PLS file, returning its phonemes and filling in `aliases`."""
    lexicon = {}
    try:
        root = ET.parse(filename).getroot()
        for lexeme in root.findall(f"{PLS_NAMESPACE}lexeme"):
            graphemes = [
                grapheme.text.strip()
                for grapheme in lexeme.findall(f"{PLS_NAMESPACE}grapheme")
                if grapheme.text is not None
            ]
            phoneme_element = lexeme.find(f"{PLS_NAMESPACE}phoneme")
            phoneme = phoneme_element.text.strip() if phoneme_element is not None and phoneme_element.text else None

            alias_element = lexeme.find(f"{PLS_NAMESPACE}alias")
            alias = alias_element.text.strip() if alias_element is not None and alias_element.text else None

            for grapheme in graphemes:
                if phoneme:
                    lexicon[grapheme] = phoneme
                if alias:
                    aliases[grapheme] = alias
    except Exception as exc:
        print(f"Error parsing PLS file {filename}: {exc}")
    return lexicon


def apply_aliases(text, aliases):
    return " ".join(aliases.get(word, word) for word in text.split())


class TTSService:
    """Runs the websocket listener on a background thread."""

    def __init__(self, on_state_change=None):
        self._thread = None
        self._ws = None
        self._stopping = False
        self._on_state_change = on_state_change
        self.running = False

    # -- lifecycle ------------------------------------------------------------

    def start(self):
        if self.running:
            return
        self._stopping = False
        self.running = True
        self._notify()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stopping = True
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
        sd.stop()
        self.running = False
        self._notify()

    def _notify(self):
        if self._on_state_change:
            self._on_state_change(self.running)

    # -- worker ---------------------------------------------------------------

    def _run(self):
        try:
            self._load()
            self._connect()
        except Exception as exc:
            print(f"TTS stopped: {exc}")
        finally:
            self.running = False
            self._notify()

    def _load(self):
        settings_path = os.path.join(paths.settings_dir(), "settings.json")
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)

        lang_code, self.male_voice, self.female_voice = REGION_VOICES.get(
            settings.get("region", "US"), REGION_VOICES["US"]
        )
        self.speed = settings.get("speed", 1.0)
        self.male_volume = settings.get("malevolume", 0.7)
        self.female_volume = settings.get("femalevolume", 0.7)
        print(f"Using male voice {self.male_voice} and female voice {self.female_voice}")

        self.voice_lookup = voice_data.build_lookup(
            voice_data.load(os.path.join(paths.settings_dir(), "voices.json"))
        )
        print(f"Loaded {len(self.voice_lookup)} character voices")

        try:
            device_name, self.sample_rate = audio_output.activate_output_device(
                settings.get("audio_device", "auto"), on_warning=print
            )
            if device_name:
                print(f"Using audio output device: {device_name}")
        except Exception as exc:
            print(f"Could not configure output device, defaulting to {KOKORO_SAMPLE_RATE}: {exc}")
            self.sample_rate = KOKORO_SAMPLE_RATE

        # Imported here rather than at module scope: it pulls in torch and takes
        # several seconds, which would otherwise stall the app before it appears.
        print("Loading the speech model, this can take a moment...")
        from kokoro import KPipeline

        self.pipeline = KPipeline(lang_code=lang_code)

        self.aliases = {}
        for name in LEXICON_DIRECTORIES:
            directory = os.path.join(paths.lexicons_dir(), name)
            for root, _, files in os.walk(directory):
                for file in files:
                    if file.endswith(".pls"):
                        path = os.path.join(root, file)
                        self.pipeline.g2p.lexicon.golds.update(parse_pls(path, self.aliases))
        print(f"Loaded pronunciation lexicons ({len(self.aliases)} replacements)")

    def voice_for(self, person, fallback):
        return self.voice_lookup.get(person.lower(), fallback)

    # -- websocket ------------------------------------------------------------

    def _connect(self):
        while not self._stopping:
            self._ws = websocket.WebSocketApp(
                WEBSOCKET_URL,
                on_open=lambda ws: print("Connected to FFXIV. Waiting for dialogue..."),
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=lambda ws, code, msg: print("Connection closed"),
            )
            self._ws.run_forever()
            if self._stopping:
                break
            print("Reconnecting in 5 seconds...")
            for _ in range(5):
                if self._stopping:
                    return
                time.sleep(1)

    def _on_error(self, ws, error):
        text = str(error)
        if "10061" in text or "refused" in text.lower():
            print("Could not reach FFXIV - is the launcher running with TTS on port 51363?")
        else:
            print(f"WebSocket error: {error}")

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)

            if data.get("Type") == "Cancel":
                print("Skipping audio")
                sd.stop()
                return

            if data.get("Type") != "Say":
                return

            speaker = data.get("Speaker", "")
            reported_gender = data.get("Voice", {}).get("Name", "").lower()
            payload = apply_aliases(data.get("Payload", ""), self.aliases)

            fallback = self.female_voice if reported_gender == "female" else self.male_voice
            voice = self.voice_for(speaker, fallback)
            print(f"{speaker or 'Unknown'}: {payload}")
            print(f"  voice: {voice}")

            # Volume follows the voice's own gender. Comparing against female_voice
            # only matched the one regional default, so every character using any
            # other female voice was played at the male volume.
            volume = (
                self.female_volume
                if voice_data.classify_gender(voice) == "female"
                else self.male_volume
            )

            for _, _, audio in self.pipeline(payload, voice=voice, speed=self.speed):
                audio = np.asarray(audio, dtype=np.float32) * volume
                audio = audio_output.resample_audio(audio, KOKORO_SAMPLE_RATE, self.sample_rate)
                sd.play(audio, samplerate=self.sample_rate)

        except Exception as exc:
            print(f"Error handling message: {exc}")

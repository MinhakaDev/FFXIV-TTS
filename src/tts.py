"""The TTS runtime: listens to the FFXIV launcher and speaks dialogue.

Wrapped in a service so the app can start and stop it without restarting, and so
loading Kokoro (slow, pulls in torch) happens on a worker thread rather than at
import time.
"""

import dataclasses
import json
import os
import queue
import threading
import time
import xml.etree.ElementTree as ET

import numpy as np
import websocket

import audio as audio_output
import paths
import voices as voice_data
from audio import KOKORO_SAMPLE_RATE

WEBSOCKET_URL = "ws://localhost:51363/Messages"
PLS_NAMESPACE = "{http://www.w3.org/2005/01/pronunciation-lexicon}"
# Whitelist, not a directory scan: lexicons/ also holds packages that deliberately
# contradict Characters-Locations-System (see the note in lexicons/README.md), and
# they must stay inert unless someone opts in by adding them here. Order matters -
# _load() merges these in sequence, so a later entry wins any grapheme collision.
LEXICON_DIRECTORIES = (
    "Characters-Locations-System",
    "Your-Name",
    "Stutter-Replacers",
    "Chat-FFXIV-Acronyms",
    "Unconfirmed-Name-Pronunciations",
    "Americanize-pronunciations",
)
REGION_VOICES = {
    "US": ("a", "am_puck", "af_heart"),
    "UK": ("b", "bm_fable", "bf_emma"),
}
# Kokoro synthesizes up to ~510 phonemes as one piece, which is most dialogue lines
# whole. Splitting at sentence ends lets the first sentence play while the rest is
# still being synthesized. The second alternative catches a sentence ending in a quote.
SENTENCE_BREAK = r'(?<=[.!?…])\s+|(?<=[.!?…]["”’])\s+'


@dataclasses.dataclass
class Line:
    text: str
    voice: str
    volume: float
    speed: float
    # Set when queued. A line whose generation is no longer current has been
    # superseded by a newer one or a Cancel, and is abandoned.
    generation: int = 0


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
    """Runs the websocket listener, and a speaker thread that synthesizes and plays
    what it queues, in the background."""

    def __init__(self, on_state_change=None):
        self._thread = None
        self._ws = None
        self._stopping = False
        self._on_state_change = on_state_change
        self.running = False
        # True once the model has loaded, until the service stops: say() and replay()
        # need it.
        self.ready = False
        # The last line from the game, kept for replay() - including one skipped before
        # it was spoken.
        self.last_line = None
        self._requests = queue.Queue()
        self._lock = threading.Lock()
        self._generation = 0
        self._player = None

    # -- lifecycle ------------------------------------------------------------

    def start(self):
        if self.running:
            return
        self._stopping = False
        self._requests = queue.Queue()
        self.running = True
        self._notify()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        # Only signals the worker: `running` stays True until _run() has actually
        # returned. Clearing it here let a Start pressed meanwhile reset _stopping and
        # revive a worker still loading the model, speaking a line or waiting to
        # reconnect, so two listeners spoke every line and Stop only closed one.
        self._stopping = True
        self.ready = False
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
        self.cancel()

    def _notify(self):
        if self._on_state_change:
            self._on_state_change(self.running)

    # -- speaking -------------------------------------------------------------

    def cancel(self):
        """Silence the line playing now and abandon any not yet spoken."""
        with self._lock:
            self._generation += 1
            if self._player is not None:
                self._player.close()
                self._player = None

    def say(self, text, voice=None, volume=None, speed=None):
        """Speak text that didn't come from the game: the GUI's previews and its Say
        box. Volume and speed default to what the game would use for this voice.
        Returns False until the model has loaded."""
        if not self.ready:
            return False
        voice = voice or self.female_voice
        default_volume, default_speed = self._playback_for(voice)
        self._enqueue(Line(
            apply_aliases(text, self.aliases),
            voice,
            default_volume if volume is None else volume,
            default_speed if speed is None else speed,
        ))
        return True

    def replay(self):
        """Speak the last line from the game again. False if there's none yet or the
        model hasn't loaded."""
        line = self.last_line
        if not self.ready or line is None:
            return False
        self._enqueue(line)
        return True

    def _enqueue(self, line):
        # A new line supersedes whatever is being synthesized. The one playing carries
        # on until the new line has audio to replace it with, as sd.play() did.
        with self._lock:
            self._generation += 1
            self._requests.put(dataclasses.replace(line, generation=self._generation))

    def _superseded(self, line):
        return self._stopping or line.generation != self._generation

    def _play(self, player, line):
        """Start this line's playback in place of the previous one's, unless it was
        superseded while its first sentence was being synthesized."""
        with self._lock:
            if self._superseded(line):
                return False
            if self._player is not None:
                self._player.close()
                self._player = None
            player.start()
            self._player = player
        return True

    def _playback_for(self, voice):
        """(volume, speed) for a voice: its own override where it has one, else the
        gender and global defaults."""
        # The fallback volume follows the voice's own gender: comparing against
        # female_voice only matched the one regional default, so every character
        # using any other female voice was played at the male volume.
        override = self.voice_settings.get(voice, {})
        volume = override.get(
            "volume",
            self.female_volume
            if voice_data.classify_gender(voice) == "female"
            else self.male_volume,
        )
        return volume, override.get("speed", self.speed)

    # -- worker ---------------------------------------------------------------

    def _run(self):
        speaker = None
        try:
            self._load()
            # Synthesis runs on its own thread so the websocket keeps reading: a Cancel
            # or the next line used to wait until the current one was fully synthesized.
            speaker = threading.Thread(target=self._speak_loop, args=(self._requests,), daemon=True)
            speaker.start()
            self.ready = True
            self._connect()
        except Exception as exc:
            print(f"TTS stopped: {exc}")
        finally:
            self.ready = False
            if speaker is not None:
                self.cancel()
                self._requests.put(None)
                speaker.join()
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
        # Per-voice {voice_id: {"volume", "speed"}} overrides layered over the
        # gender/global defaults above; voices absent here use those defaults.
        self.voice_settings = settings.get("voice_settings", {})
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

    def _speak_loop(self, requests):
        while True:
            line = requests.get()
            if line is None:
                return
            try:
                self._speak(line)
            except Exception as exc:
                print(f"Error speaking line: {exc}")

    def _speak(self, line):
        """Synthesize a line a sentence at a time, playing each as soon as it's ready."""
        if self._superseded(line):
            return
        player = None
        try:
            for _, _, audio in self.pipeline(
                line.text, voice=line.voice, speed=line.speed, split_pattern=SENTENCE_BREAK
            ):
                # Checked between sentences: a Cancel, a newer line or Stop abandons the
                # rest of this one rather than waiting for it to finish synthesizing.
                if self._superseded(line):
                    return
                audio = np.asarray(audio, dtype=np.float32) * line.volume
                audio = audio_output.resample_audio(audio, KOKORO_SAMPLE_RATE, self.sample_rate)
                if player is None:
                    player = audio_output.LinePlayer(self.sample_rate)
                    player.feed(audio)
                    if not self._play(player, line):
                        return
                else:
                    player.feed(audio)
        finally:
            # Lets the stream stop after the audio it already has, including when the
            # line was abandoned partway. A cancelled one was closed by cancel() already.
            if player is not None:
                player.finish()

    # -- websocket ------------------------------------------------------------

    def _connect(self):
        while not self._stopping:
            self._ws = websocket.WebSocketApp(
                WEBSOCKET_URL,
                on_open=self._on_open,
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

    def _on_open(self, ws):
        if self._stopping:
            # Stop landed between the loop's check and this socket existing, so its
            # close() missed it.
            ws.close()
            return
        print("Connected to FFXIV. Waiting for dialogue...")

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
                self.cancel()
                return

            if data.get("Type") != "Say" or self._stopping:
                return

            speaker = data.get("Speaker", "")
            reported_gender = data.get("Voice", {}).get("Name", "").lower()
            payload = apply_aliases(data.get("Payload", ""), self.aliases)

            fallback = self.female_voice if reported_gender == "female" else self.male_voice
            voice = self.voice_for(speaker, fallback)
            print(f"{speaker or 'Unknown'}: {payload}")
            print(f"  voice: {voice}")

            self.last_line = Line(payload, voice, *self._playback_for(voice))
            self._enqueue(self.last_line)

        except Exception as exc:
            print(f"Error handling message: {exc}")

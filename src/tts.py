"""The TTS runtime: listens to the FFXIV launcher and speaks dialogue.

Wrapped in a service so the app can start and stop it without restarting, and so
loading Kokoro (slow, pulls in torch) happens on a worker thread rather than at
import time.
"""

import dataclasses
import json
import os
import queue
import string
import threading
import time
import warnings
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
    "Qwen3-Respellings",
    "Your-Name",
    "Stutter-Replacers",
    "Chat-FFXIV-Acronyms",
    "Unconfirmed-Name-Pronunciations",
    "Americanize-pronunciations",
)
# Ours, not TextToTalk's: Characters-Locations-System's names spelled the way they sound,
# for Qwen3-TTS, which can't read their IPA. Kokoro has the IPA, so it skips these.
QWEN_ONLY_LEXICONS = {"Qwen3-Respellings"}
REGION_VOICES = {
    "US": ("a", "am_puck", "af_heart"),
    "UK": ("b", "bm_fable", "bf_emma"),
}
# settings["engine"] values for Qwen3-TTS, and the model size each loads. Anything
# else is Kokoro.
QWEN_ENGINES = {"qwen3-0.6b": "0.6b", "qwen3-1.7b": "1.7b"}
# Kokoro synthesizes up to ~510 phonemes as one piece, which is most dialogue lines
# whole. Splitting at sentence ends lets the first sentence play while the rest is
# still being synthesized. The second alternative catches a sentence ending in a quote.
SENTENCE_BREAK = r'(?<=[.!?…])\s+|(?<=[.!?…]["”’])\s+'
# Harmless warnings from loading Kokoro, which read as errors in the Run screen's log:
# torch's, about how Kokoro's model is built. Hugging Face's go through its logger.
QUIET_WARNINGS = (
    "dropout option adds dropout after all but last recurrent layer",
    r"`torch.nn.utils.weight_norm` is deprecated",
    r"`torch.jit.script` is deprecated",
)
# Stripped off a word before looking it up in the aliases, so "Minhaka," still matches.
WORD_EDGES = string.punctuation + "“”‘’…"
# Taken off a word whose alias didn't match, so "Alphinaud's" finds Alphinaud's.
POSSESSIVES = ("'s", "’s")
# How many characters the Recent screen remembers, latest first.
HEARD_LIMIT = 40


@dataclasses.dataclass
class Line:
    text: str
    voice: str
    volume: float
    speed: float
    # Who says it, when known: Qwen3-TTS gives a character their own voice.
    person: str = ""
    # Whose race voices Qwen3-TTS picks from when they have none of their own:
    # voice_data.race_of(), "" when the game didn't say.
    race: str = ""
    # Set when queued. A line whose generation is no longer current has been
    # superseded by a newer one or a Cancel, and is abandoned.
    generation: int = 0


def parse_pls(filename, aliases, use_phonemes=True):
    """Read one PLS file, returning its phonemes and filling in `aliases`.

    A lexeme with both (the Name screen writes one: IPA for Kokoro, a respelling for
    Qwen3-TTS) gives its phoneme to an engine that reads them, and its alias otherwise.
    No vendored lexeme has both.
    """
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
                if alias and not (phoneme and use_phonemes):
                    aliases[grapheme] = alias
    except Exception as exc:
        print(f"Error parsing PLS file {filename}: {exc}")
    return lexicon


def apply_aliases(text, aliases):
    return " ".join(_alias(word, aliases) for word in text.split())


def _alias(word, aliases):
    if word in aliases:
        return aliases[word]
    # Dialogue puts punctuation against names ("Minhaka!") and makes them possessive
    # ("Minhaka's"), which kept their alias from ever matching.
    core = word.strip(WORD_EDGES)
    stem = core
    for suffix in POSSESSIVES:
        if core.endswith(suffix) and core not in aliases:
            stem = core[:-len(suffix)]
    if stem and stem in aliases:
        start = word.index(core)
        return word[:start] + aliases[stem] + word[start + len(stem):]
    return word


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
        # {lowercased name: Line}, each named character's latest line, oldest first, for
        # the Recent screen. Kept across Stop and Start. heard_count goes up with every
        # line, so the screen can tell when to look again.
        self._heard = {}
        self._heard_lock = threading.Lock()
        self.heard_count = 0
        self._requests = queue.Queue()
        self._lock = threading.Lock()
        self._generation = 0
        self._player = None
        # The Qwen3-TTS engine when settings pick it; Kokoro's pipeline otherwise.
        self.qwen = None
        # settings["engine"] and ["region"] as of the last Start, which changing takes
        # a restart: the GUI compares them with what's saved.
        self.engine = None
        self.region = None

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

    @property
    def stopping(self):
        """True between stop() and the worker having exited."""
        return self.running and self._stopping

    def refresh_settings(self):
        """Apply what the GUI saved since Start: speed, volume, voices.json and the
        lexicons. The engine, region and audio device take a restart."""
        if not self.ready:
            return
        self._apply_settings(self._read_settings())
        self._load_lexicons()

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

    def say(self, text, voice=None, volume=None, speed=None, person=None):
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
            person=person or "",
        ))
        return True

    def replay(self, line=None, voice=None):
        """Speak a line from the game again: the last one, unless given one from heard().
        With a voice, in that one. Either way at the volume and speed its voice has now.
        False if there's none yet or the model hasn't loaded."""
        line = line or self.last_line
        if not self.ready or line is None:
            return False
        # Looked up again even in its own voice: a line heard under the other engine
        # kept that engine's speed.
        voice = voice or line.voice
        volume, speed = self._playback_for(voice)
        self._enqueue(dataclasses.replace(line, voice=voice, volume=volume, speed=speed))
        return True

    def heard(self):
        """Each named character's latest line, latest first."""
        with self._heard_lock:
            return list(reversed(self._heard.values()))

    def _remember(self, line):
        with self._heard_lock:
            key = line.person.lower()
            # Popped first, so speaking again moves them to the end.
            self._heard.pop(key, None)
            self._heard[key] = line
            if len(self._heard) > HEARD_LIMIT:
                del self._heard[next(iter(self._heard))]
            self.heard_count += 1

    def reload_voice(self, name):
        """Use a Qwen3-TTS voice clip the Voices screen just made, from the next line."""
        if self.qwen is not None:
            self.qwen.reload(name)

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
        # Tuning's overrides are per Kokoro voice, which Qwen3-TTS doesn't speak with, so
        # the GUI hides that screen under it and they don't apply.
        override = self.voice_settings.get(voice, {}) if self.qwen is None else {}
        volume = override.get(
            "volume",
            self.female_volume
            if voice_data.classify_gender(voice) == "female"
            else self.male_volume,
        )
        return volume, override.get("speed", self.speed if self.qwen is None else self.qwen_speed)

    # -- worker ---------------------------------------------------------------

    def _run(self):
        speaker = None
        try:
            self._load()
            # Synthesis runs on its own thread so the websocket keeps reading: a Cancel
            # or the next line used to wait until the current one was fully synthesized.
            speaker = threading.Thread(target=self._speak_loop, args=(self._requests,), daemon=True)
            speaker.start()
            if not self._stopping:
                self.ready = True
                # So the Run screen can say it's loaded.
                self._notify()
            self._connect()
        except Exception as exc:
            print(f"TTS stopped: {exc}")
        finally:
            self.ready = False
            if speaker is not None:
                self.cancel()
                self._requests.put(None)
                speaker.join()
            if self.qwen is not None:
                # Gives the GPU memory back, or switching engines would keep it.
                self.qwen.close()
                self.qwen = None
            self.running = False
            self._notify()

    def _read_settings(self):
        with open(os.path.join(paths.settings_dir(), "settings.json"), "r", encoding="utf-8") as f:
            return json.load(f)

    def _apply_settings(self, settings):
        """The settings that can change while running, since they're only looked up
        per line."""
        self.speed = settings.get("speed", 1.0)
        # Qwen3-TTS keeps its own: the engines' natural paces differ, so Kokoro's speed
        # (1.3 by default) isn't the one for it.
        self.qwen_speed = settings.get("qwen_speed", 1.0)
        self.male_volume = settings.get("malevolume", 0.7)
        self.female_volume = settings.get("femalevolume", 0.7)
        # Per-voice {voice_id: {"volume", "speed"}} overrides layered over the
        # gender/global defaults above; voices absent here use those defaults.
        self.voice_settings = settings.get("voice_settings", {})
        self.voice_lookup = voice_data.build_lookup(
            voice_data.load(os.path.join(paths.settings_dir(), "voices.json"))
        )
        print(f"Loaded {len(self.voice_lookup)} character voices")

    def _load(self):
        settings = self._read_settings()
        self.engine = settings.get("engine", "kokoro")
        self.region = settings.get("region", "US")

        lang_code, self.male_voice, self.female_voice = REGION_VOICES.get(
            self.region, REGION_VOICES["US"]
        )
        print(f"Using male voice {self.male_voice} and female voice {self.female_voice}")
        self._apply_settings(settings)

        try:
            device_name, self.sample_rate = audio_output.activate_output_device(
                settings.get("audio_device", "auto"), on_warning=print
            )
            if device_name:
                print(f"Using audio output device: {device_name}")
        except Exception as exc:
            print(f"Could not configure output device, defaulting to {KOKORO_SAMPLE_RATE}: {exc}")
            self.sample_rate = KOKORO_SAMPLE_RATE

        print("Loading the speech model, this can take a moment...")
        if self.engine in QWEN_ENGINES:
            import qwen_engine

            print(f"Using Qwen3-TTS {QWEN_ENGINES[self.engine].upper()} (test)")
            # Stop interrupts the first Start's download rather than waiting it out.
            self.qwen = qwen_engine.QwenEngine(QWEN_ENGINES[self.engine], lambda: self._stopping)
            self.synthesis_rate = qwen_engine.SAMPLE_RATE
        else:
            # Imported here rather than at module scope: it pulls in torch and takes
            # several seconds, which would otherwise stall the app before it appears.
            from huggingface_hub.utils import logging as hf_logging
            from kokoro import KPipeline

            for message in QUIET_WARNINGS:
                warnings.filterwarnings("ignore", message=message)
            # Errors only: it warned about downloading without a Hugging Face account,
            # which the model doesn't need.
            hf_logging.set_verbosity_error()
            # Named, or Kokoro prints a warning that it picked this one by default.
            self.pipeline = KPipeline(lang_code=lang_code, repo_id="hexgrad/Kokoro-82M")
            self.synthesis_rate = KOKORO_SAMPLE_RATE
        self._load_lexicons()

    def _load_lexicons(self):
        # Qwen3-TTS reads plain text, so only the lexicons' word replacements apply.
        use_phonemes = self.qwen is None
        phonemes = self.pipeline.g2p.lexicon.golds if use_phonemes else {}
        aliases = {}
        for name in LEXICON_DIRECTORIES:
            if use_phonemes and name in QWEN_ONLY_LEXICONS:
                continue
            directory = os.path.join(paths.lexicons_dir(), name)
            for root, _, files in os.walk(directory):
                for file in files:
                    if file.endswith(".pls"):
                        path = os.path.join(root, file)
                        phonemes.update(parse_pls(path, aliases, use_phonemes))
        self.aliases = aliases
        print(f"Loaded pronunciation lexicons ({len(self.aliases)} replacements)")

    def voice_for(self, person, fallback, race=""):
        """Their voice from voices.json. For anyone missing from it, one picked by their
        name, so they always sound the same: from their race's voices when the game
        says it, else from the voices for other names. The fallback if neither is known."""
        voice = self.voice_lookup.get(person.lower())
        if voice:
            return voice
        gender = voice_data.classify_gender(fallback)
        voices = voice_data.RACE_VOICES.get(race, {}).get(gender)
        if voices:
            return voice_data.voice_by_name(person, voices)
        if person:
            return voice_data.voice_by_name(person, voice_data.NAMED_VOICES[gender])
        return fallback

    def _speak_loop(self, requests):
        while True:
            line = requests.get()
            if line is None:
                return
            try:
                self._speak(line)
            except Exception as exc:
                print(f"Error speaking line: {exc}")

    def _synthesize(self, line):
        """The line's audio a piece at a time: a sentence from Kokoro, a few frames
        from Qwen3-TTS, which streams."""
        if self.qwen is not None:
            return self.qwen.speak(
                line.text, line.voice, line.person, line.race, line.speed,
                lambda: self._superseded(line),
            )
        return (
            audio for _, _, audio in self.pipeline(
                line.text, voice=line.voice, speed=line.speed, split_pattern=SENTENCE_BREAK
            )
        )

    def _speak(self, line):
        """Synthesize a line a piece at a time, playing each as soon as it's ready."""
        if self._superseded(line):
            return
        player = None
        chunks = self._synthesize(line)
        try:
            for audio in chunks:
                # Checked between pieces: a Cancel, a newer line or Stop abandons the
                # rest of this one rather than waiting for it to finish synthesizing.
                if self._superseded(line):
                    return
                audio = np.asarray(audio, dtype=np.float32) * line.volume
                audio = audio_output.resample_audio(audio, self.synthesis_rate, self.sample_rate)
                if player is None:
                    player = audio_output.LinePlayer(self.sample_rate)
                    player.feed(audio)
                    if not self._play(player, line):
                        return
                else:
                    player.feed(audio)
        finally:
            # Stops a Qwen3-TTS synthesis still running for an abandoned line.
            chunks.close()
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

            race = voice_data.race_of(data.get("Race"), data.get("BodyType"))

            fallback = self.female_voice if reported_gender == "female" else self.male_voice
            voice = self.voice_for(speaker, fallback, race)
            print(f"{speaker or 'Unknown'}{f' ({race})' if race else ''}: {payload}")
            print(f"  voice: {self.qwen.voice_name(speaker, voice, race) if self.qwen else voice}")

            self.last_line = Line(payload, voice, *self._playback_for(voice), person=speaker, race=race)
            self._enqueue(self.last_line)
            if speaker:
                self._remember(self.last_line)

        except Exception as exc:
            print(f"Error handling message: {exc}")

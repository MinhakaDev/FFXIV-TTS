"""Qwen3-TTS through qwentts.cpp's C library, the engine under test beside Kokoro.

Nothing to set up for the user: the first Start with this engine downloads the library
(built by .github/workflows/qwen3-runtime.yml) and the models into qwen3-tts/. The
voices ship with the app in qwen3-voices/: short clips made once by
tools/design_voices.py, which the Base model clones so a character sounds the same from
one line to the next.
"""

import ctypes
import hashlib
import json
import os
import queue
import re
import sys
import threading
import urllib.error
import urllib.request
import wave

import numpy as np

import audio as audio_output
import paths
import updates
import voices as voice_data

# The qwentts.cpp commit whose qwen.h the ctypes structs below mirror, so check them
# against that header before changing it. tools/setup_qwen3.sh builds this commit and
# the qwen3-runtime workflow publishes it, both reading it from here.
QWENTTS_COMMIT = "de2ad833e9bd5c06082231d1da637789f7562816"
# Tagged by commit, so every version of the app downloads the build it was written for.
RUNTIME_URL = (
    "https://github.com/MinhakaDev/FFXIV-TTS/releases/download/"
    f"qwen3-runtime-{QWENTTS_COMMIT[:7]}/"
)
RUNTIME_ASSETS = {"linux": "libqwen-linux-x64.so", "win32": "qwen-windows-x64.dll"}
# One revision of the model repo, so no file can change under the checksums below.
MODELS_URL = (
    "https://huggingface.co/Serveurperso/Qwen3-TTS-GGUF/resolve/"
    "b7ee2e8c7459c3bea99da23e3d178125a7d1713c/"
)
MODELS = {
    "qwen-tokenizer-12hz-Q8_0.gguf": (
        291150624, "1883beeed99348fc35e23dd225e9082f93f6f8c109330a33d935baa8acdbfd94"),
    "qwen-talker-0.6b-base-Q8_0.gguf": (
        992615488, "d54dbaf10591421fa764ed630d764efa717ae40cd959bd48c66d4eb1af226426"),
    "qwen-talker-1.7b-base-Q8_0.gguf": (
        2079448256, "4b9a33a236908dd9435a42f7a396e38038329d053b704342a6413c08544c4fda"),
    # Only tools/design_voices.py uses this one.
    "qwen-talker-1.7b-voicedesign-Q8_0.gguf": (
        2042833824, "575610ab1ddcca4dca6bd9a64bcd859d93bbad8764f9cab24e1dbc0c51f62276"),
}

SAMPLE_RATE = 24000
LANGUAGE = b"english"
ABI_VERSION = 6
STATUS_CANCELLED = -5
LOG_WARN = 2
LIBRARY = {"win32": "qwen.dll", "darwin": "libqwen.dylib"}.get(sys.platform, "libqwen.so")
# Preferred first when several quantizations of a model are there. Downloads are Q8_0;
# the others are only found if put there by hand.
QUANTS = ("Q8_0", "Q4_K_M", "BF16", "F32")
# Who speaks for a character with no clip of their own, by the gender of their Kokoro
# voice: made on the Voices screen like anyone's.
GENDER_VOICES = {"female": "default female", "male": "default male"}
# What a named character without a clip of their own speaks with, picked by
# voice_data.voice_by_name(): the clips named after Kokoro voices, designed from the
# descriptions in designs.json. Listed rather than read from the folder: adding or
# removing one moves most characters to another clip.
NAMED_VOICES = {
    "female": (
        "af_aoede", "af_bella", "af_heart", "af_kore", "af_nicole", "af_nova",
        "af_sarah", "af_sky", "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    ),
    "male": (
        "am_echo", "am_eric", "am_fenrir", "am_michael", "am_puck",
        "bm_daniel", "bm_fable", "bm_george", "bm_lewis", "zm_yunjian",
    ),
}
# How many voices each of voice_data.RACES and AGES has for each gender: race_voices().
# Fixed, like NAMED_VOICES.
RACE_VOICE_COUNT = 2
# The library's INFO log is chatty; these say which device loaded and how fast each
# line was.
LOG_SHOWN = ("[Load]", "[Perf] Total")
# misaki's "[word](/phonemes/)" markup, from the Name screen's Preview, means nothing
# to this model: keep the word.
PHONEME_MARKUP = re.compile(r"\[([^\]]+)\]\(/[^)]*/\)")
# What a designed voice says in its clip, when designs.json doesn't exist to say it. Long
# enough (about ten seconds) for the clone to catch the voice.
REFERENCE_TEXT = (
    "The road ahead is long, and none of us can say what waits at its end. "
    "But whatever comes, we shall face it together, as we always have."
)
# The fastest speed speak() clones a clip at. The clone only partly follows a sped-up
# clip (1.3 spoke at about 1.2, 1.6 at about 1.45), and faster than this the male
# voices measured got no faster, then slower.
MAX_SPEED = 1.6
# Held back before playback starts: the first streamed chunks are one or two 80 ms
# frames, and playing them as they come stutters while the stream gets going.
PREBUFFER = SAMPLE_RATE * 4 // 10


class InitParams(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_int),
        ("talker_path", ctypes.c_char_p),
        ("codec_path", ctypes.c_char_p),
        ("use_fa", ctypes.c_bool),
        ("clamp_fp16", ctypes.c_bool),
        ("max_batch", ctypes.c_int),
        ("codec_chunk_sec", ctypes.c_float),
        ("device", ctypes.c_char_p),
    ]


class Audio(ctypes.Structure):
    _fields_ = [
        ("samples", ctypes.POINTER(ctypes.c_float)),
        ("n_samples", ctypes.c_int),
        ("sample_rate", ctypes.c_int),
        ("channels", ctypes.c_int),
    ]


class VoiceRef(ctypes.Structure):
    _fields_ = [
        ("ref_spk_emb", ctypes.POINTER(ctypes.c_float)),
        ("ref_spk_dim", ctypes.c_int),
        ("ref_codes", ctypes.POINTER(ctypes.c_int32)),
        ("ref_T", ctypes.c_int),
        ("num_codebooks", ctypes.c_int),
    ]


CANCEL_CB = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p)
CHUNK_CB = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.c_void_p)
LOG_CB = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p)


class TTSParams(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_int),
        ("text", ctypes.c_char_p),
        ("lang", ctypes.c_char_p),
        ("instruct", ctypes.c_char_p),
        ("speaker", ctypes.c_char_p),
        ("ref_audio_24k", ctypes.POINTER(ctypes.c_float)),
        ("ref_n_samples", ctypes.c_int),
        ("ref_text", ctypes.c_char_p),
        ("seed", ctypes.c_int64),
        ("max_new_tokens", ctypes.c_int),
        ("temperature", ctypes.c_float),
        ("top_k", ctypes.c_int),
        ("top_p", ctypes.c_float),
        ("repetition_penalty", ctypes.c_float),
        ("subtalker_temperature", ctypes.c_float),
        ("subtalker_top_k", ctypes.c_int),
        ("subtalker_top_p", ctypes.c_float),
        ("dump_dir", ctypes.c_char_p),
        ("cancel", CANCEL_CB),
        ("cancel_user_data", ctypes.c_void_p),
        ("on_chunk", CHUNK_CB),
        ("on_chunk_user_data", ctypes.c_void_p),
        ("ref_spk_emb", ctypes.POINTER(ctypes.c_float)),
        ("ref_spk_dim", ctypes.c_int),
        ("ref_codes", ctypes.POINTER(ctypes.c_int32)),
        ("ref_T", ctypes.c_int),
    ]


def voices_dir():
    return paths.qwen_voices_dir()


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(url, path, label, cancelled, size=None, sha256=None):
    """Fetch url to path, reporting progress in the log. An interrupted download is kept
    as <path>.part and picks up where it stopped next time."""
    part = path + ".part"
    done = os.path.getsize(part) if os.path.exists(part) else 0
    if not (size and done >= size):
        headers = {"User-Agent": "ffxiv-tts"}
        if done:
            headers["Range"] = f"bytes={done}-"
        request = urllib.request.Request(url, headers=headers)
        # updates' TLS context: the Linux build's own OpenSSL can't find Arch's or
        # Fedora's CA certificates.
        with urllib.request.urlopen(request, timeout=30, context=updates.tls_context()) as response:
            if response.status != 206:
                done = 0  # the server ignored the range, so this is the whole file
            total = size or done + int(response.headers.get("Content-Length") or 0)
            shown = None
            with open(part, "ab" if done else "wb") as f:
                while True:
                    if cancelled():
                        raise RuntimeError(f"Stopped downloading {label}. The next Start carries on.")
                    block = response.read(1 << 20)
                    if not block:
                        break
                    f.write(block)
                    done += len(block)
                    percent = done * 100 // total // 5 * 5 if total else None
                    if percent is not None and percent != shown:
                        shown = percent
                        print(f"Downloading {label}: {percent}% of {total / 1e6:.0f} MB")
    if sha256:
        print(f"Checking {label}...")
        if _sha256(part) != sha256:
            os.remove(part)
            raise RuntimeError(f"{label} didn't download correctly. Press Start to try again.")
    os.replace(part, path)


def model_path(prefix, cancelled=lambda: False):
    """The GGUF named <prefix>-<quant>.gguf, downloading the Q8_0 one if none is there."""
    for quant in QUANTS:
        path = os.path.join(paths.qwen_dir(), f"{prefix}-{quant}.gguf")
        if os.path.exists(path):
            return path
    name = f"{prefix}-Q8_0.gguf"
    size, sha256 = MODELS[name]
    path = os.path.join(paths.qwen_dir(), name)
    os.makedirs(paths.qwen_dir(), exist_ok=True)
    _download(MODELS_URL + name, path, name, cancelled, size, sha256)
    return path


def ensure_library(cancelled=lambda: False):
    """Download the library unless it's there: built by tools/setup_qwen3.sh, or an
    earlier download."""
    path = os.path.join(paths.qwen_dir(), LIBRARY)
    if os.path.exists(path):
        return
    asset = RUNTIME_ASSETS.get(sys.platform)
    if asset is None:
        raise RuntimeError("Qwen3-TTS isn't available for this system.")
    os.makedirs(paths.qwen_dir(), exist_ok=True)
    try:
        _download(RUNTIME_URL + asset, path, "the Qwen3-TTS library", cancelled)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        raise RuntimeError(
            f"The Qwen3-TTS library for commit {QWENTTS_COMMIT[:7]} isn't published yet: run the "
            "qwen3-runtime workflow, or build it here with tools/setup_qwen3.sh."
        ) from None


def pending_download(names):
    """MB still to download for these model files, to say so before starting."""
    present = {
        name for name in names
        if any(os.path.exists(os.path.join(paths.qwen_dir(), name.replace("Q8_0", quant)))
               for quant in QUANTS)
    }
    return sum(MODELS[name][0] for name in names if name not in present) / 1e6


def engine_models(size):
    """The model files QwenEngine loads for a size, as MODELS names them."""
    return [f"qwen-talker-{size}-base-Q8_0.gguf", "qwen-tokenizer-12hz-Q8_0.gguf"]


def has_library():
    return os.path.exists(os.path.join(paths.qwen_dir(), LIBRARY))


def clip_names():
    """Every voice with a clip in voices_dir(), as its file is named."""
    if not os.path.isdir(voices_dir()):
        return []
    return sorted(
        name for name, extension in map(os.path.splitext, os.listdir(voices_dir()))
        if extension.lower() == ".wav"
    )


def race_voices(race, gender):
    """The clips for one of voice_data.RACES or AGES and a gender: "lalafell female 1"..."""
    return tuple(f"{race} {gender} {n}" for n in range(1, RACE_VOICE_COUNT + 1))


def read_wav(path):
    """A 16-bit WAV as mono float32 at the model's 24 kHz."""
    with wave.open(path, "rb") as f:
        if f.getsampwidth() != 2:
            raise ValueError(f"{path} isn't 16-bit PCM")
        samples = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2").astype(np.float32) / 32768
        samples = samples.reshape(-1, f.getnchannels()).mean(axis=1)
        return audio_output.resample_audio(samples, f.getframerate(), SAMPLE_RATE)


def _clip(name):
    """(wav path, transcript or None) for a voice in voices_dir(). Without a transcript
    a clip still works, cloning the timbre only (the model's x-vector mode)."""
    text = None
    transcript = os.path.join(voices_dir(), f"{name}.txt")
    if os.path.exists(transcript):
        with open(transcript, "r", encoding="utf-8") as f:
            text = f.read().strip() or None
    return os.path.join(voices_dir(), f"{name}.wav"), text


def designs_path():
    return os.path.join(voices_dir(), "designs.json")


def load_designs():
    """designs.json: {"reference_text": what every clip says, "voices": {name: design}},
    each design normalized to {"instruct": description, "seed": n}."""
    try:
        with open(designs_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {"reference_text": REFERENCE_TEXT, "voices": {}}
    data["voices"] = {
        name: {"instruct": design, "seed": 1} if isinstance(design, str) else {"seed": 1, **design}
        for name, design in data["voices"].items()
    }
    return data


def save_design(name, instruct, seed):
    data = load_designs()
    data["voices"][name] = {"instruct": instruct, "seed": seed}
    os.makedirs(voices_dir(), exist_ok=True)
    with open(designs_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def write_clip(name, samples, text):
    """Save a voice clip and the transcript the clone reads alongside it."""
    os.makedirs(voices_dir(), exist_ok=True)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(os.path.join(voices_dir(), f"{name}.wav"), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(pcm.tobytes())
    with open(os.path.join(voices_dir(), f"{name}.txt"), "w", encoding="utf-8") as f:
        f.write(text)


def _log(level, message, _):
    text = message.decode("utf-8", "replace")
    if level >= LOG_WARN or text.startswith(LOG_SHOWN):
        print(f"Qwen3-TTS {text}")


# Module level: the library keeps calling it, so it must outlive any one Model.
_log_callback = LOG_CB(_log)
_library = None


def _load_library():
    global _library
    if _library is not None:
        return _library
    lib = ctypes.CDLL(os.path.join(paths.qwen_dir(), LIBRARY))
    lib.qt_last_error.restype = ctypes.c_char_p
    lib.qt_init_default_params.argtypes = [ctypes.POINTER(InitParams)]
    lib.qt_init.argtypes = [ctypes.POINTER(InitParams)]
    lib.qt_init.restype = ctypes.c_void_p
    lib.qt_free.argtypes = [ctypes.c_void_p]
    lib.qt_extract_voice_ref.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.POINTER(VoiceRef),
    ]
    lib.qt_voice_ref_free.argtypes = [ctypes.POINTER(VoiceRef)]
    lib.qt_tts_default_params.argtypes = [ctypes.POINTER(TTSParams)]
    lib.qt_synthesize.argtypes = [ctypes.c_void_p, ctypes.POINTER(TTSParams), ctypes.POINTER(Audio)]
    lib.qt_audio_free.argtypes = [ctypes.POINTER(Audio)]
    lib.qt_log_set.argtypes = [LOG_CB, ctypes.c_void_p]
    lib.qt_log_set(_log_callback, None)
    _library = lib
    return lib


class Model:
    """One talker GGUF loaded with the codec, on the best device found. ensure_library()
    must have run."""

    def __init__(self, talker_path, codec_path):
        self.lib = _load_library()
        params = InitParams()
        self.lib.qt_init_default_params(ctypes.byref(params))
        params.talker_path = talker_path.encode()
        params.codec_path = codec_path.encode()
        self.ctx = self.lib.qt_init(ctypes.byref(params))
        if not self.ctx:
            raise RuntimeError(f"Qwen3-TTS failed to load {talker_path}: {self.error()}")
        self._references = []

    def error(self):
        return (self.lib.qt_last_error() or b"").decode("utf-8", "replace")

    def close(self):
        for reference in self._references:
            self.lib.qt_voice_ref_free(ctypes.byref(reference))
        self._references = []
        if self.ctx:
            self.lib.qt_free(self.ctx)
            self.ctx = None

    def params(self, text, **fields):
        params = TTSParams()
        self.lib.qt_tts_default_params(ctypes.byref(params))
        params.text = text.encode("utf-8")
        params.lang = LANGUAGE
        for name, value in fields.items():
            setattr(params, name, value.encode("utf-8") if isinstance(value, str) else value)
        return params

    def reference(self, wav_path, speed=1.0):
        """Extract the cloning latents from a clip, sped up `speed` times: the clone
        takes its pace from the clip. Freed by close()."""
        samples = audio_output.stretch(read_wav(wav_path), speed, SAMPLE_RATE)
        samples = np.ascontiguousarray(samples, dtype=np.float32)
        reference = VoiceRef()
        status = self.lib.qt_extract_voice_ref(
            self.ctx, samples.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(samples),
            ctypes.byref(reference),
        )
        if status:
            raise RuntimeError(f"Could not read the voice in {wav_path}: {self.error()}")
        self._references.append(reference)
        return reference

    def generate(self, params):
        """Synthesize all of it, then return it."""
        out = Audio()
        if self.lib.qt_synthesize(self.ctx, ctypes.byref(params), ctypes.byref(out)):
            raise RuntimeError(f"Qwen3-TTS failed: {self.error()}")
        try:
            return np.ctypeslib.as_array(out.samples, shape=(out.n_samples,)).copy()
        finally:
            self.lib.qt_audio_free(ctypes.byref(out))

    def stream(self, params, cancelled):
        """Yield the audio as it's synthesized. Closing the generator, or `cancelled()`
        turning true, stops the synthesis within a frame."""
        chunks = queue.Queue()
        stop = threading.Event()
        result = {}

        # Both run on the library's compute thread, so they only hand off and return.
        def on_chunk(samples, count, _):
            chunks.put(np.ctypeslib.as_array(samples, shape=(count,)).copy())
            return not stop.is_set()

        def on_cancel(_):
            return stop.is_set() or cancelled()

        params.on_chunk = CHUNK_CB(on_chunk)
        params.cancel = CANCEL_CB(on_cancel)

        def run():
            result["status"] = self.lib.qt_synthesize(self.ctx, ctypes.byref(params), None)
            # qt_last_error is per thread: read it on the one that failed.
            result["error"] = self.error()
            chunks.put(None)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        try:
            pending = []
            while True:
                chunk = chunks.get()
                if chunk is None:
                    break
                if pending is None:
                    yield chunk
                    continue
                pending.append(chunk)
                if sum(len(c) for c in pending) >= PREBUFFER:
                    yield np.concatenate(pending)
                    pending = None
            if pending:
                yield np.concatenate(pending)
        finally:
            stop.set()
            worker.join()
        if result["status"] not in (0, STATUS_CANCELLED):
            raise RuntimeError(f"Qwen3-TTS failed: {result['error']}")


class QwenEngine:
    """Speaks with the Base model, cloning each character's clip from voices_dir().
    Downloads whatever it's missing first, giving up early once `cancelled()`."""

    def __init__(self, size, cancelled):
        # Looked for first, so a missing voices folder doesn't cost a 1 GB download.
        # {name: (wav path, transcript or None)}.
        self.clips = {name.lower(): _clip(name) for name in clip_names()}
        if not self.clips:
            raise RuntimeError(
                f"No Qwen3-TTS voices in {voices_dir()}. Make them with tools/design_voices.py."
            )

        pending = pending_download(engine_models(size))
        if pending:
            print(f"Qwen3-TTS downloads {pending / 1000:.1f} GB the first time. This can take a while.")
        ensure_library(cancelled)
        codec_path = model_path("qwen-tokenizer-12hz", cancelled)
        self.model = Model(model_path(f"qwen-talker-{size}-base", cancelled), codec_path)
        print(f"Loaded {len(self.clips)} Qwen3-TTS voices")
        # {name: (speed, latents)}, for the speed last spoken at.
        self._latents = {}

    def close(self):
        self.model.close()

    def reload(self, name):
        """Pick up a clip that was just made or replaced, from the next line on."""
        self.clips[name] = _clip(name)
        self._latents.pop(name, None)

    def voice_name(self, person, voice, race=""):
        """The clip a character speaks with: their own. Without one, a clip picked by
        their name, so they always sound the same: from their race's (race_voices())
        when the game says it, else from NAMED_VOICES. The default for their Kokoro
        voice's gender if neither is known."""
        gender = voice_data.classify_gender(voice)
        person = (person or "").lower()
        names = [person]
        if race:
            names.append(voice_data.voice_by_name(person, race_voices(race, gender)))
        if person:
            names.append(voice_data.voice_by_name(person, NAMED_VOICES[gender]))
        for name in names + [GENDER_VOICES[gender]]:
            if name in self.clips:
                return name
        return next(iter(self.clips))

    def speak(self, text, voice, person, race, speed, cancelled):
        """The line's audio as it's synthesized, at about `speed` times the pace of the
        voice's clip. The model has no speed of its own, but it speaks at the pace of the
        clip it clones, so it clones one sped up. Stretching its output afterwards
        instead sounded clearly worse. Only a clip with a transcript passes its pace on."""
        name = self.voice_name(person, voice, race)
        speed = round(min(speed, MAX_SPEED), 2)
        # Read once: reload() on the UI thread can drop the entry between a check and
        # a second lookup, which lost the line to a KeyError.
        cached = self._latents.get(name)
        if cached is not None and cached[0] == speed:
            reference = cached[1]
        else:
            # Extracted on first use rather than at Start, which would load every clip.
            reference = self.model.reference(self.clips[name][0], speed)
            self._latents[name] = (speed, reference)
        transcript = self.clips[name][1]
        fields = {"ref_spk_emb": reference.ref_spk_emb, "ref_spk_dim": reference.ref_spk_dim}
        if transcript:
            fields.update(ref_codes=reference.ref_codes, ref_T=reference.ref_T, ref_text=transcript)
        params = self.model.params(PHONEME_MARKUP.sub(r"\1", text), **fields)
        return self.model.stream(params, cancelled)


class Designer:
    """Invents voices from descriptions with the VoiceDesign model, downloading it (and
    the library and codec) the first time. Separate from QwenEngine: it's only loaded
    while voices are being made, so it doesn't hold 2 GB of the GPU otherwise."""

    def __init__(self, cancelled=lambda: False):
        ensure_library(cancelled)
        codec_path = model_path("qwen-tokenizer-12hz", cancelled)
        self.model = Model(model_path("qwen-talker-1.7b-voicedesign", cancelled), codec_path)

    def close(self):
        self.model.close()

    def design(self, instruct, text, seed):
        """`text` spoken by the voice `instruct` describes. The same seed gives the same
        voice again; another seed, another voice that fits the description."""
        return self.model.generate(self.model.params(text, instruct=instruct, seed=seed))

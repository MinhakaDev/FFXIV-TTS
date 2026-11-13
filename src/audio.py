import collections
import re
import threading

import numpy as np
import sounddevice as sd

KOKORO_SAMPLE_RATE = 24000


def normalize_device_name(name):
    # ALSA card/device indices ("(hw:0,0)") shift between boots, replugs, and when a
    # wireless headset sleeps and wakes, so a saved name stops matching the same
    # physical device. Compare without them.
    return re.sub(r'\s*\(hw:\d+,\d+\)\s*$', '', name).strip().lower()


def refresh_devices():
    """Re-scan the system for audio devices.

    PortAudio caches its device list when the library initialises, so a wireless
    headset that is switched on (or wakes from sleep) after launch stays invisible
    until it is re-initialised.
    """
    try:
        sd._terminate()
        sd._initialize()
        return True
    except Exception:
        return False


def output_devices():
    return [(i, d) for i, d in enumerate(sd.query_devices()) if d['max_output_channels'] > 0]


def output_device_names():
    try:
        return [d['name'] for _, d in output_devices()]
    except Exception:
        return []


def select_output_device(devices, configured, on_warning=None):
    """Index of the output device to use, or None to leave the current default alone.

    "default" is a static ALSA alias that doesn't necessarily route to whatever output
    the user actually has selected in their desktop's audio settings, so playback can
    silently go nowhere. Respect an explicit settings.json override first; otherwise
    prefer a PipeWire/PulseAudio device, which does route through the real audio server.
    """
    configured = (configured or "auto").strip()
    outputs = [(i, d) for i, d in enumerate(devices) if d['max_output_channels'] > 0]

    if configured.lower() != "auto":
        target = normalize_device_name(configured)
        for matches in (
            lambda d: d['name'] == configured,
            lambda d: normalize_device_name(d['name']) == target,
            lambda d: target in normalize_device_name(d['name']),
        ):
            for i, d in outputs:
                if matches(d):
                    return i

        if on_warning:
            available = "\n".join(f"  - {d['name']}" for _, d in outputs)
            on_warning(
                f"Configured audio_device '{configured}' not found. Available output devices:\n"
                f"{available}\nFalling back to auto-detection."
            )

    for name in ('pipewire', 'pulse'):
        for i, d in outputs:
            if name in d['name'].lower():
                return i
    return None


def activate_output_device(configured, on_warning=None):
    """Point sounddevice at the configured device. Returns (device_name, sample_rate)."""
    devices = sd.query_devices()
    index = select_output_device(devices, configured, on_warning)
    name = None
    if index is not None:
        _, current_input = sd.default.device
        sd.default.device = (current_input, index)
        name = devices[index]['name']
    return name, int(sd.query_devices(kind='output')['default_samplerate'])


def resample_audio(audio, orig_sr, target_sr):
    if orig_sr == target_sr:
        return audio
    target_length = int(round(len(audio) * target_sr / orig_sr))
    orig_indices = np.arange(len(audio))
    target_indices = np.linspace(0, len(audio) - 1, num=target_length)
    return np.interp(target_indices, orig_indices, audio).astype(audio.dtype)


class TimeStretch:
    """Speeds speech up or slows it down without changing its pitch, fed whole or a
    chunk at a time: for Qwen3-TTS's voice clips, whose pace the clone copies.
    Resampling would raise the pitch along with the pace.

    WSOLA: overlapping frames are taken from the input `speed` times further apart than
    they're laid down, each moved a little to where its waveform lines up with the one
    before, so the voice's pitch periods carry on across the joins instead of clicking.
    """

    def __init__(self, speed, sample_rate):
        self.speed = speed
        # 40 ms holds a few pitch periods of even a low voice.
        self.frame = sample_rate // 25
        self.hop = self.frame // 2
        # How far a frame may move to line up: ±10 ms, a pitch period or more.
        self.tolerance = self.frame // 4
        self.window = np.hanning(self.frame + 1)[:-1].astype(np.float32)
        # Positions below count from the start of the input with this padding in front,
        # so the first frames can search before the start.
        self._input = np.zeros(self.tolerance, dtype=np.float32)
        self._start = 0  # where _input[0] is
        self._received = 0
        self._frames = 0
        self._previous = None  # where the last frame was taken from
        self._output = np.zeros(self.frame, dtype=np.float32)
        self._weight = np.zeros(self.frame, dtype=np.float32)
        self._emitted = 0

    def feed(self, audio):
        """The stretched audio this input completes, which may be none yet."""
        audio = np.asarray(audio, dtype=np.float32)
        self._input = np.concatenate((self._input, audio))
        self._received += len(audio)
        pieces = []
        while self._ready():
            pieces.append(self._step())
        return np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)

    def flush(self):
        """The rest, once the input has ended: all of it comes to `speed` times shorter."""
        target = round(self._received / self.speed)
        pieces = []
        while self._emitted < target:
            if not self._ready():
                self._input = np.concatenate((self._input, np.zeros(self.frame, dtype=np.float32)))
                continue
            pieces.append(self._step())
        audio = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
        return audio[:len(audio) - (self._emitted - target)]

    def _nominal(self, frame):
        return self.tolerance + round(frame * self.hop * self.speed)

    def _slice(self, position, length):
        return self._input[position - self._start:position - self._start + length]

    def _ready(self):
        end = self._nominal(self._frames) + self.tolerance + self.frame
        if self._previous is not None:
            end = max(end, self._previous + self.hop + self.frame)
        return end <= self._start + len(self._input)

    def _step(self):
        """Lay down the next frame, returning the output it completes."""
        nominal = self._nominal(self._frames)
        if self._previous is None:
            position = nominal
        else:
            # What followed the last frame in the input joins on to it seamlessly, so
            # the frame near `nominal` most like that is the one taken.
            follows = self._slice(self._previous + self.hop, self.frame)
            search = self._slice(nominal - self.tolerance, self.frame + 2 * self.tolerance)
            position = nominal - self.tolerance + int(np.argmax(np.correlate(search, follows, "valid")))
        self._previous = position
        self._frames += 1
        self._output += self._slice(position, self.frame) * self.window
        self._weight += self.window
        done = self._output[:self.hop] / np.maximum(self._weight[:self.hop], 1e-3)
        silence = np.zeros(self.hop, dtype=np.float32)
        self._output = np.concatenate((self._output[self.hop:], silence))
        self._weight = np.concatenate((self._weight[self.hop:], silence))
        self._emitted += self.hop
        # Drop the input no later frame reads.
        keep = min(self._nominal(self._frames) - self.tolerance, position + self.hop)
        if keep > self._start:
            self._input = self._input[keep - self._start:]
            self._start = keep
        return done


def loudness(audio, sample_rate):
    """Speech's level in dB below full scale: the mean power of its 20 ms frames within
    40 dB of the loudest, so pauses don't count. None for silence."""
    frame = sample_rate // 50
    frames = audio[:len(audio) // frame * frame].reshape(-1, frame)
    power = np.mean(frames.astype(np.float64) ** 2, axis=1)
    if not len(power) or power.max() == 0:
        return None
    return 10 * np.log10(np.mean(power[power > power.max() * 1e-4]))


def normalize(audio, sample_rate, target):
    """Speech scaled to `target` dB, or as near as its peaks allow without clipping."""
    level = loudness(audio, sample_rate)
    if level is None:
        return audio
    return audio * min(10 ** ((target - level) / 20), 1 / np.abs(audio).max())


def stretch(audio, speed, sample_rate):
    """Speech `speed` times faster, at the same pitch."""
    if abs(speed - 1) < 0.01:
        return audio
    stretcher = TimeStretch(speed, sample_rate)
    return np.concatenate((stretcher.feed(audio), stretcher.flush()))


class LinePlayer:
    """Plays one line through a single output stream while it's still being synthesized.

    Chunks are fed in as they're ready and played back to back, so speech starts after
    the first sentence rather than the whole line. One stream per line also keeps the
    fix for long lines: calling sd.play() per chunk stopped the chunk before it.
    """

    def __init__(self, sample_rate):
        self.sample_rate = sample_rate
        self._chunks = collections.deque()
        self._finished = False
        self._lock = threading.Lock()
        self._stream = None

    def feed(self, audio):
        with self._lock:
            self._chunks.append(audio)

    def finish(self):
        """No more chunks are coming: the stream stops once it has played what it has."""
        with self._lock:
            self._finished = True

    def start(self):
        self._stream = sd.OutputStream(
            samplerate=self.sample_rate, channels=1, dtype="float32", callback=self._fill,
        )
        self._stream.start()

    def close(self):
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass

    def _fill(self, outdata, frames, time, status):
        # Runs on PortAudio's thread. Outputs silence while the next sentence is still
        # being synthesized, rather than stopping.
        written = 0
        with self._lock:
            while written < frames and self._chunks:
                chunk = self._chunks[0]
                take = min(frames - written, len(chunk))
                outdata[written:written + take, 0] = chunk[:take]
                written += take
                if take == len(chunk):
                    self._chunks.popleft()
                else:
                    self._chunks[0] = chunk[take:]
            done = self._finished and not self._chunks
        outdata[written:] = 0
        if done:
            raise sd.CallbackStop


def _output_for(configured):
    """(device index or None for the default, its sample rate) for playing outside the
    TTS service, which may not have picked the device yet."""
    devices = sd.query_devices()
    index = select_output_device(devices, configured)
    sample_rate = int(
        devices[index]['default_samplerate'] if index is not None
        else sd.query_devices(kind='output')['default_samplerate']
    )
    return index, sample_rate


def play_samples(audio, sample_rate, configured):
    """Play audio through the configured device, stopping what the last call played."""
    index, device_rate = _output_for(configured)
    sd.play(resample_audio(audio, sample_rate, device_rate), samplerate=device_rate, device=index)


def play_test_tone(configured, seconds=0.5, frequency=440.0):
    """Play a short tone through the configured device. Returns the device name used."""
    index, sample_rate = _output_for(configured)

    t = np.linspace(0, seconds, int(sample_rate * seconds), endpoint=False)
    tone = 0.3 * np.sin(2 * np.pi * frequency * t)

    # Fade the edges so the test doesn't start and end with an audible click.
    fade = max(1, int(sample_rate * 0.02))
    envelope = np.ones_like(tone)
    envelope[:fade] = np.linspace(0, 1, fade)
    envelope[-fade:] = np.linspace(1, 0, fade)

    sd.play((tone * envelope).astype(np.float32), samplerate=sample_rate, device=index)
    return sd.query_devices(index)['name'] if index is not None else "system default"

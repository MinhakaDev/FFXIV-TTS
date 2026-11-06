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


def play_test_tone(configured, seconds=0.5, frequency=440.0):
    """Play a short tone through the configured device. Returns the device name used."""
    devices = sd.query_devices()
    index = select_output_device(devices, configured)
    sample_rate = int(
        devices[index]['default_samplerate'] if index is not None
        else sd.query_devices(kind='output')['default_samplerate']
    )

    t = np.linspace(0, seconds, int(sample_rate * seconds), endpoint=False)
    tone = 0.3 * np.sin(2 * np.pi * frequency * t)

    # Fade the edges so the test doesn't start and end with an audible click.
    fade = max(1, int(sample_rate * 0.02))
    envelope = np.ones_like(tone)
    envelope[:fade] = np.linspace(0, 1, fade)
    envelope[-fade:] = np.linspace(1, 0, fade)

    sd.play((tone * envelope).astype(np.float32), samplerate=sample_rate, device=index)
    return devices[index]['name'] if index is not None else "system default"

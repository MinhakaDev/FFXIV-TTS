import os
import sys


def base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def settings_dir():
    return os.path.join(base_dir(), "settings")


def lexicons_dir():
    return os.path.join(base_dir(), "lexicons")


def qwen_dir():
    """Where Qwen3-TTS's library and models are downloaded to (see qwen_engine.py)."""
    return os.path.join(base_dir(), "qwen3-tts")


def qwen_voices_dir():
    """The Qwen3-TTS voice clips, which ship with the app like lexicons/."""
    return os.path.join(base_dir(), "qwen3-voices")


def defaults_dir():
    """The shipped settings a frozen build carries inside itself (see defaults.py)."""
    return os.path.join(sys._MEIPASS, "defaults")

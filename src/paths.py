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


def defaults_dir():
    """The shipped settings a frozen build carries inside itself (see defaults.py)."""
    return os.path.join(sys._MEIPASS, "defaults")

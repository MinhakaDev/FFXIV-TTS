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

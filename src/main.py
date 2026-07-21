"""Entry point for the FFXIV TTS app.

Opens the window by default. Pass --headless to run the TTS on the console only,
without a GUI (useful over SSH or for a machine with no display).
"""

import io
import sys


def _ensure_streams():
    """Give the process real stdout/stderr objects.

    A windowed PyInstaller build has sys.stdout set to None, so any print() -
    including ones inside torch and kokoro - would raise AttributeError.
    """
    if sys.stdout is None:
        sys.stdout = io.StringIO()
    if sys.stderr is None:
        sys.stderr = sys.stdout


def main():
    _ensure_streams()

    if "--headless" in sys.argv:
        import tts

        service = tts.TTSService()
        service.start()
        try:
            while service.running:
                service._thread.join(timeout=0.5)
        except KeyboardInterrupt:
            print("Stopping...")
            service.stop()
        return

    import settings_gui

    settings_gui.run()


if __name__ == "__main__":
    main()

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


def _attach_console():
    """Give --headless somewhere to print on Windows.

    The Windows exe is built windowed (console=False), so it starts without a console
    and everything headless mode printed was lost. Attach to the console of the terminal
    it was started from, or open a console window of its own when there is none.
    """
    import ctypes

    kernel32 = ctypes.windll.kernel32
    attach_parent_process = -1
    if not kernel32.AttachConsole(attach_parent_process) and not kernel32.AllocConsole():
        return
    try:
        sys.stdout = sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        pass  # _ensure_streams() still gives print() somewhere to go


def main():
    headless = "--headless" in sys.argv
    if headless and sys.platform == "win32" and sys.stdout is None:
        _attach_console()
    _ensure_streams()

    # Before anything reads settings/: merge in what this release's defaults add.
    import defaults

    defaults.install()

    if headless:
        import tts
        import updates

        def report_update(result):
            if result:
                print(updates.notice(*result))

        service = tts.TTSService()
        service.start()
        # In the background, after the TTS is already starting: a slow or unreachable
        # GitHub held up startup by several seconds or more, and Ctrl+C during that wait
        # escaped the handler below as a raw traceback.
        updates.check_async(report_update)
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

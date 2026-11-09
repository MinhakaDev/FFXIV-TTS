import json
import os
import queue
import random
import sys
import threading
import time
import tkinter as tk
import webbrowser
import xml.etree.ElementTree as ET
from tkinter import messagebox
from xml.sax.saxutils import escape

import customtkinter as ctk

import audio as audio_output
import paths
import qwen_engine
import tts
import updates
import voices as voice_data

# Females first, then males, best grade first within each - so the picker is grouped
# by gender and the better-sounding voices are nearest the top.
VOICE_CHOICES = [voice_data.describe_voice(v) for v in voice_data.voice_options()]

SETTINGS_PATH = os.path.join(paths.settings_dir(), "settings.json")
VOICES_PATH = os.path.join(paths.settings_dir(), "voices.json")
NAME_LEXICON_PATH = os.path.join(paths.lexicons_dir(), "Your-Name", "lexicon.pls")

# CTkButton's default text_color is near-white in *both* appearance modes, which is
# only legible on the filled blue buttons. Any button with a transparent or light-gray
# fill (nav buttons, outline buttons) needs a theme-aware text colour or it turns into
# white-on-light and vanishes in Light mode.
MUTED_TEXT = ("gray10", "gray90")

# What Voice Tuning's Preview says: two sentences, so there's enough to judge the speed.
PREVIEW_LINE = "Well met, adventurer. The Scions have need of you once more."

# General's engine picker: label -> settings["engine"] (see tts.QWEN_ENGINES).
ENGINES = {"Kokoro": "kokoro", "Qwen3 0.6B": "qwen3-0.6b", "Qwen3 1.7B": "qwen3-1.7b"}
ENGINE_LABELS = {engine: label for label, engine in ENGINES.items()}
# {"default female": "female", ...}: Qwen3's voices for whoever has none of their own,
# which the Run screen's Say box offers under Qwen3 in place of Kokoro's.
DEFAULT_GENDERS = {name: gender for gender, name in qwen_engine.GENDER_VOICES.items()}
QWEN_SAY_CHOICES = [name.capitalize() for name in DEFAULT_GENDERS]

DEFAULT_SETTINGS = {
    "engine": "kokoro",
    "region": "US",
    "speed": 1.0,
    "malevolume": 0.7,
    "femalevolume": 0.7,
    "audio_device": "auto",
    "appearance": "Dark",
    # {voice_id: {"volume": float, "speed": float}} - per-voice overrides layered over
    # the region/gender defaults above. Voices absent here fall back to those defaults.
    "voice_settings": {},
}


def load_settings():
    settings = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            settings.update(json.load(f))
    except FileNotFoundError:
        pass
    return settings


def update_settings(**changes):
    # Re-read before writing so each screen only touches its own keys - otherwise
    # saving one screen would clobber changes another screen already wrote.
    settings = load_settings()
    settings.update(changes)
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=4)
    return settings


def is_qwen(engine):
    return engine in tts.QWEN_ENGINES


def saved_engine():
    """The engine the next Start loads. Screens follow this one, so they show the
    settings that apply to it."""
    return load_settings().get("engine", "kokoro")


def load_records():
    """voices.json as a flat, editable list of {expansion, person, voice}."""
    data = voice_data.load(VOICES_PATH)
    records = []
    for expansion in sorted(
        data, key=lambda e: voice_data.EXPANSIONS.index(e) if e in voice_data.EXPANSIONS else len(voice_data.EXPANSIONS)
    ):
        for person, voice in sorted(data[expansion].items()):
            records.append({"expansion": expansion, "person": person, "voice": voice})
    return records


def save_records(records):
    data = {}
    for record in records:
        person = record["person"].strip()
        voice = record["voice"].strip()
        if not person or not voice:
            continue
        data.setdefault(record["expansion"], {})[person] = voice
    voice_data.save(VOICES_PATH, data)


def build_name_lexicon(name, pronunciation, respelling):
    """The name's lexeme: the IPA for Kokoro, and the respelling, an alias, for Qwen3-TTS,
    which reads plain text. tts.parse_pls() gives each engine its own."""
    name_upper = escape(name[0].upper() + name[1:])
    name_lower = escape(name[0].lower() + name[1:])
    entries = "".join(
        f"\n        <{tag}>{escape(value)}</{tag}>"
        for tag, value in (("phoneme", pronunciation), ("alias", respelling)) if value
    )
    return f"""<?xml version='1.0' encoding='UTF-8'?>
<lexicon xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" version="1.0"
    xsi:schemaLocation="http://www.w3.org/2005/01/pronunciation-lexicon http://www.w3.org/TR/2007/CR-pronunciation-lexicon-20071212/pls.xsd"
    alphabet="ipa" xml:lang="en" xmlns="http://www.w3.org/2005/01/pronunciation-lexicon">
    <lexeme>
        <grapheme>{name_lower}</grapheme>
        <grapheme>{name_upper}</grapheme>{entries}
    </lexeme>
</lexicon>"""


def load_name_lexicon():
    """(name, IPA, respelling) as the Name screen last saved them, blank where unset."""
    try:
        lexeme = ET.parse(NAME_LEXICON_PATH).getroot().find(f"{tts.PLS_NAMESPACE}lexeme")
    except (OSError, ET.ParseError):
        return "", "", ""
    if lexeme is None:
        return "", "", ""

    def text(tag):
        element = lexeme.find(f"{tts.PLS_NAMESPACE}{tag}")
        return (element.text or "").strip() if element is not None else ""

    # The capitalized grapheme, written last, is the name as it appears in game.
    graphemes = [g.text.strip() for g in lexeme.findall(f"{tts.PLS_NAMESPACE}grapheme") if g.text]
    return (graphemes[-1] if graphemes else ""), text("phoneme"), text("alias")


def speech_ready(service):
    """True when the speech model is loaded, so previews can play. Otherwise says why
    not, offering to start it."""
    if service.ready:
        return True
    if service.running:
        messagebox.showinfo("Still loading", "The speech model is still loading. Try again in a moment.")
    elif messagebox.askyesno(
        "Start the speech model?",
        f"Previews use the speech model ({ENGINE_LABELS.get(saved_engine(), 'Kokoro')}), which "
        "loads when the TTS starts. The game doesn't need to be running.\n\n"
        "Start it now? Try again once the Run screen says it's running.",
    ):
        service.start()
    return False


def open_in_browser(url):
    """webbrowser.open(), with the system's library path for whatever it launches.

    A frozen Linux build runs with LD_LIBRARY_PATH pointing into its bundle, and child
    processes inherit it: xdg-open's /bin/sh and the browser then load the bundle's (the
    CI runner's) libraries and can die before the page opens. PyInstaller keeps the
    original value in LD_LIBRARY_PATH_ORIG.
    """
    if not (getattr(sys, "frozen", False) and sys.platform.startswith("linux")):
        return webbrowser.open(url)
    saved = os.environ.pop("LD_LIBRARY_PATH", None)
    original = os.environ.get("LD_LIBRARY_PATH_ORIG")
    if original is not None:
        os.environ["LD_LIBRARY_PATH"] = original
    try:
        return webbrowser.open(url)
    finally:
        os.environ.pop("LD_LIBRARY_PATH", None)
        if saved is not None:
            os.environ["LD_LIBRARY_PATH"] = saved


class Tooltip:
    """Text that appears under a widget while the pointer rests on it."""

    DELAY_MS = 350

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.window = None
        self.pending = None
        # CTk widgets bind with add=True already; their hover colours keep working.
        widget.bind("<Enter>", lambda _: self._schedule(), add="+")
        widget.bind("<Leave>", self._leave, add="+")
        widget.bind("<ButtonPress>", lambda _: self.hide(), add="+")

    def _schedule(self):
        if self.pending is None and self.window is None:
            self.pending = self.widget.after(self.DELAY_MS, self.show)

    def _leave(self, event):
        # A CTk button is a canvas with a label on it, and crossing from one to the
        # other fires Leave: only hide once the pointer is off the whole widget.
        under = self.widget.winfo_containing(event.x_root, event.y_root)
        path = str(self.widget)
        if under is not None and (str(under) == path or str(under).startswith(path + ".")):
            return
        self.hide()

    def show(self):
        self.pending = None
        dark = ctk.get_appearance_mode() == "Dark"
        self.window = window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        # The window's own colour shows as a one-pixel border around the label.
        window.configure(bg="gray35" if dark else "gray65")
        frame = ctk.CTkFrame(window, corner_radius=0, fg_color="gray17" if dark else "gray97")
        frame.pack(padx=1, pady=1)
        ctk.CTkLabel(
            frame, text=self.text, justify="left", anchor="w",
            text_color="gray90" if dark else "gray10", font=ctk.CTkFont(size=12),
        ).pack(padx=12, pady=10)
        window.wm_geometry(
            f"+{self.widget.winfo_rootx()}+{self.widget.winfo_rooty() + self.widget.winfo_height() + 6}"
        )

    def hide(self):
        if self.pending is not None:
            self.widget.after_cancel(self.pending)
            self.pending = None
        if self.window is not None:
            self.window.destroy()
            self.window = None


class LogStream:
    """Tees writes to the on-screen log, keeping the real stream when there is one."""

    def __init__(self, sink, original=None):
        self.sink = sink
        self.original = original
        self._pending = ""

    def write(self, text):
        if self.original is not None:
            try:
                self.original.write(text)
            except Exception:
                pass
        self._pending += text
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            if line.strip():
                self.sink(line)

    def flush(self):
        if self.original is not None:
            try:
                self.original.flush()
            except Exception:
                pass

    def isatty(self):
        # Not a terminal. tqdm (huggingface_hub's model download) only turns its progress
        # bars off when told so; otherwise their carriage-return redraws piled up in
        # _pending and the log got nothing for a minute, then one huge line per file.
        return False


class Screen(ctk.CTkFrame):
    """A page in the sidebar, with a title and a consistent content column."""

    title = ""
    subtitle = ""

    def __init__(self, parent, service):
        super().__init__(parent, fg_color="transparent")
        self.service = service
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 0))
        ctk.CTkLabel(
            header, text=self.title, font=ctk.CTkFont(size=22, weight="bold"), anchor="w"
        ).pack(anchor="w")
        # Kept, since some screens reword it for the engine.
        self.subtitle_label = ctk.CTkLabel(
            header, text=self.subtitle, font=ctk.CTkFont(size=12),
            text_color=("gray45", "gray60"), anchor="w", justify="left",
        )
        if self.subtitle:
            self.subtitle_label.pack(anchor="w", pady=(2, 0))

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=28, pady=18)
        self.body.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._row_widgets = {}

    def show_row(self, row, visible):
        """Show or hide everything on one row of the body, for settings that only
        apply to one engine."""
        # Collected while the row is still shown: grid_slaves() skips hidden widgets.
        if row not in self._row_widgets:
            self._row_widgets[row] = self.body.grid_slaves(row=row)
        for widget in self._row_widgets[row]:
            # grid_remove() keeps each widget's options, so a bare grid() puts it back.
            if visible:
                widget.grid()
            else:
                widget.grid_remove()

    def field(self, row, label):
        ctk.CTkLabel(self.body, text=label, anchor="w").grid(
            row=row, column=0, sticky="w", pady=10, padx=(0, 18)
        )

    def slider(self, row, label, value, from_, to):
        self.field(row, label)
        holder = ctk.CTkFrame(self.body, fg_color="transparent")
        holder.grid(row=row, column=1, sticky="ew")
        holder.grid_columnconfigure(0, weight=1)

        readout = ctk.CTkLabel(holder, text=f"{value:.1f}", width=34)
        readout.grid(row=0, column=1, padx=(12, 0))

        var = tk.DoubleVar(value=value)
        # Trace the var (not the slider's command) so the readout also tracks a
        # programmatic .set() - the Voice Tuning screen resets the sliders that way.
        var.trace_add("write", lambda *_: readout.configure(text=f"{var.get():.1f}"))
        ctk.CTkSlider(
            holder, from_=from_, to=to, variable=var, number_of_steps=int((to - from_) * 20),
        ).grid(row=0, column=0, sticky="ew")
        return var

    def save_button(self, row, command, text="Save"):
        ctk.CTkButton(self.body, text=text, command=command, height=38, width=130).grid(
            row=row, column=0, columnspan=2, pady=(28, 0)
        )

    def on_show(self):
        """Called each time the sidebar switches to this screen."""

    def on_hide(self):
        """Called each time the sidebar switches away from this screen."""

    def hint(self, row, text):
        label = ctk.CTkLabel(
            self.body, text=text, font=ctk.CTkFont(size=11), text_color=("gray45", "gray60"),
            anchor="w", justify="left",
        )
        label.grid(row=row, column=1, sticky="w")
        return label


class RunScreen(Screen):
    title = "FFXIV TTS"
    subtitle = "Start FFXIV and log in, then press Start."

    def __init__(self, parent, service):
        super().__init__(parent, service)
        self.messages = queue.Queue()
        self.update_result = queue.Queue()
        # Set by restart(): start again once the stop it asked for has finished.
        self.restart_pending = False

        # Update banner: gridded on row 0 but removed until a newer release is found,
        # so it takes no space when there's nothing to announce.
        self.update_url = None
        self.banner = ctk.CTkFrame(self.body, fg_color=("#d8ecff", "#1f3d57"))
        self.banner.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        self.banner.grid_columnconfigure(0, weight=1)
        self.banner_label = ctk.CTkLabel(
            self.banner, text="", anchor="w", justify="left", text_color=MUTED_TEXT,
        )
        self.banner_label.grid(row=0, column=0, sticky="w", padx=12, pady=8)
        ctk.CTkButton(
            self.banner, text="Download", width=100, height=28, command=self.open_download,
        ).grid(row=0, column=1, padx=(0, 6), pady=8)
        ctk.CTkButton(
            self.banner, text="✕", width=28, height=28, fg_color="transparent",
            border_width=1, text_color=MUTED_TEXT, command=self.banner.grid_remove,
        ).grid(row=0, column=2, padx=(0, 8), pady=8)
        self.banner.grid_remove()

        self.status = ctk.CTkLabel(
            self.body, text="Stopped", font=ctk.CTkFont(size=15, weight="bold"), anchor="w"
        )
        self.status.grid(row=1, column=0, sticky="w")

        controls = ctk.CTkFrame(self.body, fg_color="transparent")
        controls.grid(row=1, column=1, sticky="e")
        ctk.CTkButton(
            controls, text="Replay last line", command=self.replay, height=42, width=150,
            fg_color="transparent", border_width=1, text_color=MUTED_TEXT,
        ).pack(side="left", padx=(0, 10))
        self.button = ctk.CTkButton(
            controls, text="Start", command=self.toggle, height=42, width=150,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.button.pack(side="left")

        self.log = ctk.CTkTextbox(
            self.body, fg_color=("gray92", "gray14"), font=ctk.CTkFont(family="monospace", size=11),
            wrap="word",
        )
        self.log.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(16, 0))
        self.log.configure(state="disabled")
        self.body.grid_rowconfigure(2, weight=1)
        self.body.grid_columnconfigure(0, weight=1)

        # Speaks whatever is typed, through the same lexicons as the game's lines.
        say = ctk.CTkFrame(self.body, fg_color="transparent")
        say.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        say.grid_columnconfigure(0, weight=1)
        self.say_text = ctk.CTkEntry(say, placeholder_text="Type something to hear it spoken")
        self.say_text.grid(row=0, column=0, sticky="ew")
        self.say_text.bind("<Return>", lambda _: self.say())
        self.say_voice = ctk.CTkOptionMenu(say, values=VOICE_CHOICES, width=200)
        self.say_voice.set(VOICE_CHOICES[0])
        self.say_voice.grid(row=0, column=1, padx=8)
        ctk.CTkButton(
            say, text="Say", command=self.say, width=80,
            fg_color="transparent", border_width=1, text_color=MUTED_TEXT,
        ).grid(row=0, column=2)

        self.append("Press Start to begin. The speech model loads on the first run,")
        self.append("which can take a minute and needs an internet connection.")
        self.drain()

        # Check GitHub for a newer build off the UI thread; check() no-ops on a dev build.
        # The result comes back through a queue that drain() polls. Calling self.after()
        # from the worker instead raises if it lands before mainloop() has started - and
        # a dev build's check returns at once, while the other screens are still being built.
        updates.check_async(self.update_result.put)

    def on_show(self):
        # General may have picked another engine: list its voices, and say which loads.
        choices = QWEN_SAY_CHOICES if is_qwen(saved_engine()) else VOICE_CHOICES
        if list(self.say_voice.cget("values")) != choices:
            self.say_voice.configure(values=choices)
            self.say_voice.set(choices[0])
        self.show_state()

    def show_update(self, result):
        if not result:
            return
        tag, url = result
        self.update_url = url
        self.banner_label.configure(text=f"Update available: {tag}. You have {updates.__version__}.")
        self.banner.grid()
        # The link goes in the log as well, where it can be copied if no browser opens.
        self.append(updates.notice(tag, url))

    def open_download(self):
        url = self.update_url or updates.RELEASES_PAGE
        if not open_in_browser(url):
            self.append(f"Couldn't open a browser. Download the update from {url}")

    def append(self, line):
        self.messages.put(line)

    def drain(self):
        """Tk isn't thread-safe, so other threads queue log lines and the update result
        for this to apply on the UI thread."""
        lines = []
        while True:
            try:
                lines.append(self.messages.get_nowait())
            except queue.Empty:
                break
        if lines:
            self.log.configure(state="normal")
            self.log.insert("end", "\n".join(lines) + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        try:
            self.show_update(self.update_result.get_nowait())
        except queue.Empty:
            pass
        self.after(150, self.drain)

    def toggle(self):
        if self.service.running:
            self.append("Stopping...")
            # The worker may still be loading the model or finishing a line; it reports
            # back through show_state() once it has really exited.
            self.status.configure(text="Stopping...")
            self.button.configure(state="disabled")
            self.service.stop()
        else:
            self.service.start()

    def restart(self):
        """Stop, then Start once the worker has exited, so a new engine or region loads."""
        if not self.service.running:
            self.service.start()
            return
        self.restart_pending = True
        self.toggle()

    def replay(self):
        if speech_ready(self.service) and not self.service.replay():
            self.append("Nothing to replay yet: no line has come from the game.")

    def say(self):
        text = self.say_text.get().strip()
        if not text or not speech_ready(self.service):
            return
        choice = self.say_voice.get()
        default = DEFAULT_GENDERS.get(choice.lower())
        if default:
            # Qwen3's default for a gender speaks for any voice of that gender.
            voice = self.service.female_voice if default == "female" else self.service.male_voice
        else:
            voice = voice_data.voice_from_description(choice)
        self.service.say(text, voice)

    def show_state(self):
        """Reads the service rather than taking the state it notified: notifications
        queue up, and only the latest state matters."""
        service = self.service
        if not service.running:
            if self.restart_pending:
                self.restart_pending = False
                service.start()
                return
            # The engine is picked on General; say which one Start loads.
            text = f"Stopped. Start loads {ENGINE_LABELS.get(saved_engine(), 'Kokoro')}."
        elif service.stopping:
            text = "Stopping..."
        elif service.ready:
            text = f"Running: {ENGINE_LABELS.get(service.engine, 'Kokoro')}"
        else:
            text = "Loading the speech model..."
        self.status.configure(text=text)
        self.button.configure(
            text="Stop" if service.running else "Start",
            state="disabled" if service.stopping else "normal",
        )


class GeneralScreen(Screen):
    title = "General"
    subtitle = ("Speech engine, language and how loud each voice speaks.\n"
                "Hover over an engine to compare them.")

    ABOUT = {
        "kokoro": "Characters speak with the Kokoro voices you assign them on the Voices screen.",
        "qwen3": "A test. Each character speaks with a voice of their own, made on the Voices\n"
                 "screen, and the rest with a default female or male one.",
    }
    # Shown while the pointer rests on an engine.
    COMPARISON = {
        "Kokoro": "Kokoro: fast and light, but plainer\n\n"
                  "+ Runs well on the processor: no graphics card needed\n"
                  "+ Quick to start speaking, and leaves the graphics card to the game\n"
                  "+ Small download (about 330 MB)\n"
                  "+ Speed setting, and names said exactly from IPA\n"
                  "- Flatter, less expressive than Qwen3\n"
                  "- A few dozen stock voices, shared between characters",
        "Qwen3 0.6B": "Qwen3 0.6B: more natural voices, the lighter Qwen3\n\n"
                      "+ More natural and expressive than Kokoro\n"
                      "+ Every character can have a voice of their own\n"
                      "+ Faster and lighter than 1.7B\n"
                      "- Needs a graphics card to keep up: slow on the processor\n"
                      "- Rougher than 1.7B\n"
                      "- Downloads 1.3 GB, and uses graphics memory the game wants\n"
                      "- No speed setting, and still a test",
        "Qwen3 1.7B": "Qwen3 1.7B: the best voices, the heaviest\n\n"
                      "+ The most natural and expressive voices here\n"
                      "+ Every character can have a voice of their own\n"
                      "- Needs a graphics card, with more memory than 0.6B\n"
                      "- Slower than 0.6B to start each line\n"
                      "- Downloads 2.4 GB\n"
                      "- No speed setting, and still a test",
    }
    # Kokoro's alone: the region picks its default voices and accent.
    REGION_ROW, SPEED_ROW = 3, 4

    def __init__(self, parent, service):
        super().__init__(parent, service)
        settings = load_settings()

        self.field(0, "Speech engine")
        self.engine = ctk.CTkSegmentedButton(
            self.body, values=list(ENGINES), text_color=MUTED_TEXT,
            command=lambda _: self.pick_engine(),
        )
        self.engine.set(ENGINE_LABELS.get(settings.get("engine", "kokoro"), "Kokoro"))
        self.engine.grid(row=0, column=1, sticky="w")
        # _buttons_dict is CTkSegmentedButton's {value: button}: it has no public way to
        # reach one segment.
        self.tooltips = [
            Tooltip(button, self.COMPARISON[label])
            for label, button in getattr(self.engine, "_buttons_dict", {}).items()
        ]
        self.about = self.hint(1, "")
        self.download = self.hint(2, "")

        self.field(self.REGION_ROW, "Region")
        self.region = ctk.CTkSegmentedButton(
            self.body, values=["US", "UK"], width=200, text_color=MUTED_TEXT,
        )
        self.region.set(settings.get("region", "US"))
        self.region.grid(row=self.REGION_ROW, column=1, sticky="w")

        self.speed = self.slider(self.SPEED_ROW, "Speech speed", settings.get("speed", 1.0), 0.5, 2.0)
        self.male_volume = self.slider(5, "Male volume", settings.get("malevolume", 0.7), 0.0, 2.0)
        self.female_volume = self.slider(6, "Female volume", settings.get("femalevolume", 0.7), 0.0, 2.0)

        buttons = ctk.CTkFrame(self.body, fg_color="transparent")
        buttons.grid(row=7, column=0, columnspan=2, pady=(28, 0))
        ctk.CTkButton(
            buttons, text="Preview", command=self.preview, height=38, width=110,
            fg_color="transparent", border_width=1, text_color=MUTED_TEXT,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkButton(buttons, text="Save", command=self.save, height=38, width=130).pack(side="left")

        self.show_engine()

    def on_show(self):
        # A download finished, or the Run screen started an engine, since last shown.
        self.show_engine()

    def picked(self):
        return ENGINES[self.engine.get()]

    def pick_engine(self):
        """Picking an engine selects it at once: it's saved, the sidebar changes to suit
        it, and a running TTS restarts with it. Saved only on Save, the screen showed one
        engine while Start loaded another."""
        engine = self.picked()
        self.show_engine()
        if engine == saved_engine():
            return
        update_settings(engine=engine)
        self.master.apply_engine()
        if self.service.running:
            run = self.master.screens["Run"]
            run.append(f"Switching to {ENGINE_LABELS[engine]}...")
            run.restart()

    def show_engine(self):
        """Lay the screen out for the engine picked."""
        engine = self.picked()
        qwen = is_qwen(engine)
        self.about.configure(text=self.ABOUT["qwen3" if qwen else "kokoro"])
        self.show_row(self.REGION_ROW, not qwen)
        self.show_row(self.SPEED_ROW, not qwen)

        status = []
        if qwen:
            pending = qwen_engine.pending_download(qwen_engine.engine_models(tts.QWEN_ENGINES[engine]))
            if pending:
                status.append(f"Its first Start downloads {pending / 1000:.1f} GB.")
            elif not qwen_engine.has_library():
                status.append("Downloaded, but for the Qwen3-TTS library, which its first Start fetches.")
            else:
                status.append("Downloaded.")
        if self.service.running and self.service.engine == engine:
            status.append("Running now.")
        self.download.configure(text=" ".join(status))
        self.show_row(2, bool(status))

    def preview(self):
        # The sliders as they are, saved or not, with the region's default female voice.
        if speech_ready(self.service):
            self.service.say(
                PREVIEW_LINE, tts.REGION_VOICES[self.region.get()][2],
                volume=round(self.female_volume.get(), 2), speed=round(self.speed.get(), 2),
            )

    def save(self):
        # The engine was saved when it was picked.
        region = self.region.get()
        update_settings(
            region=region,
            speed=round(self.speed.get(), 2),
            malevolume=round(self.male_volume.get(), 2),
            femalevolume=round(self.female_volume.get(), 2),
        )
        if not self.service.running:
            messagebox.showinfo("Saved", "General settings saved.")
            return
        # Speed and volume apply from the next line; Kokoro's region needs the model
        # loaded again.
        self.service.refresh_settings()
        if region == self.service.region or is_qwen(self.service.engine):
            messagebox.showinfo("Saved", "General settings saved.")
        elif messagebox.askyesno("Saved", f"General settings saved.\n\nRestart the TTS now to use the {region} voices?"):
            self.master.screens["Run"].restart()


class VoiceTuningScreen(Screen):
    title = "Voice Tuning"
    subtitle = "Override volume and speed for one voice at a time.\nVoices you leave alone use the General defaults."

    def __init__(self, parent, service):
        super().__init__(parent, service)
        self.overrides = {v: dict(o) for v, o in load_settings().get("voice_settings", {}).items()}
        self.current = None
        self.shown = None

        self.field(0, "Voice")
        self.voice = ctk.CTkOptionMenu(
            self.body, values=VOICE_CHOICES, width=260,
            command=lambda _: self.load_voice(),
        )
        self.voice.set(VOICE_CHOICES[0])
        self.voice.grid(row=0, column=1, sticky="w")

        volume, speed = self._defaults_for(voice_data.voice_from_description(VOICE_CHOICES[0]))
        self.volume = self.slider(1, "Volume", volume, 0.0, 2.0)
        self.speed = self.slider(2, "Speed", speed, 0.5, 2.0)

        buttons = ctk.CTkFrame(self.body, fg_color="transparent")
        buttons.grid(row=3, column=0, columnspan=2, pady=(28, 0))
        ctk.CTkButton(
            buttons, text="Preview", command=self.preview, height=38, width=110,
            fg_color="transparent", border_width=1, text_color=MUTED_TEXT,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkButton(
            buttons, text="Reset to default", command=self.reset, height=38, width=150,
            fg_color="transparent", border_width=1, text_color=MUTED_TEXT,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkButton(buttons, text="Save", command=self.save, height=38, width=130).pack(side="left")

        self.load_voice()

    def on_show(self):
        # The General screen may have saved new defaults since this one was drawn.
        self.load_voice()

    def _defaults_for(self, voice):
        # Read fresh rather than once at construction: General can save new defaults
        # while this screen stays built, and stale copies made Save drop overrides that
        # matched the old values and store speeds that no longer applied.
        settings = load_settings()
        gender_key = "femalevolume" if voice_data.classify_gender(voice) == "female" else "malevolume"
        return settings.get(gender_key, 0.7), settings.get("speed", 1.0)

    def _show(self, volume, speed):
        self.volume.set(volume)
        self.speed.set(speed)
        self.shown = (round(volume, 2), round(speed, 2))

    def _commit_current(self):
        """Fold the sliders the user actually moved into the on-screen voice's override."""
        if self.current is None:
            return
        override = self.overrides.get(self.current, {})
        for key, var, shown in zip(("volume", "speed"), (self.volume, self.speed), self.shown):
            if round(var.get(), 2) != shown:
                override[key] = round(var.get(), 2)
        if override:
            self.overrides[self.current] = override

    def load_voice(self):
        # Commit the voice we were showing before moving to the new one, so edits
        # survive switching between voices without a save in between.
        self._commit_current()
        self.current = voice_data.voice_from_description(self.voice.get())
        override = self.overrides.get(self.current, {})
        volume, speed = self._defaults_for(self.current)
        # An override only holds what differs from the defaults, so either key may be absent.
        self._show(override.get("volume", volume), override.get("speed", speed))

    def preview(self):
        # The sliders as they are now, saved or not, so a value can be heard before saving.
        if speech_ready(self.service):
            self.service.say(
                PREVIEW_LINE, voice_data.voice_from_description(self.voice.get()),
                volume=round(self.volume.get(), 2), speed=round(self.speed.get(), 2),
            )

    def reset(self):
        voice = voice_data.voice_from_description(self.voice.get())
        self.overrides.pop(voice, None)
        self._show(*self._defaults_for(voice))

    def save(self):
        self._commit_current()
        # Keep only the values that differ from the General defaults, so a voice whose
        # speed was left alone keeps following the default speed, and tweaking then
        # reverting a voice doesn't freeze it against later changes to the defaults.
        kept = {}
        for voice, override in self.overrides.items():
            defaults = dict(zip(("volume", "speed"), self._defaults_for(voice)))
            changed = {
                key: round(value, 2) for key, value in override.items()
                if key in defaults and round(value, 2) != round(defaults[key], 2)
            }
            if changed:
                kept[voice] = changed
        self.overrides = kept
        update_settings(voice_settings=self.overrides)
        self.service.refresh_settings()
        messagebox.showinfo("Saved", "Voice tuning saved.")


class AudioScreen(Screen):
    title = "Audio"
    subtitle = "Where the speech is played. Use Test sound to check it before saving."

    def __init__(self, parent, service):
        super().__init__(parent, service)
        settings = load_settings()

        self.field(0, "Output device")
        picker = ctk.CTkFrame(self.body, fg_color="transparent")
        picker.grid(row=0, column=1, sticky="ew")
        picker.grid_columnconfigure(0, weight=1)

        self.device = ctk.CTkComboBox(picker, values=self.device_choices(), width=290)
        self.device.set(settings.get("audio_device", "auto"))
        self.device.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(
            picker, text="Refresh", command=self.refresh, width=80, height=28,
            fg_color="transparent", border_width=1, text_color=MUTED_TEXT,
        ).grid(row=0, column=1, padx=(8, 0))

        ctk.CTkLabel(
            self.body,
            text='"auto" follows whatever output your system is already using, and is\n'
                 "almost always the right choice - it also keeps working when a wireless\n"
                 "headset sleeps and wakes.\n\n"
                 "Headsets often do not appear in this list at all: while PipeWire or\n"
                 "PulseAudio owns the device, its raw entry is hidden here. That is normal.\n"
                 'Leave this on "auto" and pick the headset in your system sound settings.\n\n'
                 "Press Refresh if you connected a device just now.",
            font=ctk.CTkFont(size=11), text_color=("gray45", "gray60"),
            anchor="w", justify="left",
        ).grid(row=1, column=1, sticky="w", pady=(6, 0))

        buttons = ctk.CTkFrame(self.body, fg_color="transparent")
        buttons.grid(row=2, column=0, columnspan=2, pady=(28, 0))
        ctk.CTkButton(
            buttons, text="Test sound", command=self.test, height=38, width=130,
            fg_color="transparent", border_width=1, text_color=MUTED_TEXT,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkButton(buttons, text="Save", command=self.save, height=38, width=130).pack(side="left")

        self.status = ctk.CTkLabel(self.body, text="", anchor="w", justify="left")
        self.status.grid(row=3, column=0, columnspan=2, sticky="w", pady=(16, 0))

    def device_choices(self):
        return ["auto"] + audio_output.output_device_names()

    def refresh(self):
        selected = self.device.get()
        audio_output.refresh_devices()
        choices = self.device_choices()
        self.device.configure(values=choices)
        # Keep the current pick if it survived the rescan, so refreshing doesn't
        # silently reset a device the user already chose.
        self.device.set(selected if selected in choices else "auto")
        found = len(choices) - 1
        self.status.configure(
            text=f"Found {found} output device{'s' if found != 1 else ''}.",
            text_color=("gray30", "gray70"),
        )

    def test(self):
        try:
            used = audio_output.play_test_tone(self.device.get())
        except Exception as exc:
            self.status.configure(text=f"Could not play through this device:\n{exc}", text_color="#e04f4f")
            return
        self.status.configure(text=f"Played a test tone through: {used}", text_color=("gray30", "gray70"))

    def save(self):
        device = self.device.get().strip() or "auto"
        changed = device != load_settings().get("audio_device", "auto")
        update_settings(audio_device=device)
        # The TTS picks its device at Start.
        if changed and self.service.running:
            if messagebox.askyesno("Saved", "Audio device saved.\n\nRestart the TTS now to play through it?"):
                self.master.screens["Run"].restart()
        else:
            messagebox.showinfo("Saved", "Audio device saved.")


class NameScreen(Screen):
    title = "Name Pronunciation"
    subtitle = "Teach the TTS how to say your character's name."

    INTRO = {
        "kokoro": "Ask ChatGPT how to pronounce your name in the IPA alphabet,\n"
                  'e.g. "Minhaka" becomes "miˈɲaka". Test it at ipa-reader.com.',
        "qwen3": "Qwen3 reads plain text, so spell your name the way it sounds,\n"
                 'e.g. "Minhaka" as "Mee-nyah-ka". Preview it until it sounds right.',
    }
    IPA_ROW, RESPELLING_ROW = 2, 3

    def __init__(self, parent, service):
        super().__init__(parent, service)

        self.intro = ctk.CTkLabel(
            self.body, text="", font=ctk.CTkFont(size=12), text_color=("gray45", "gray60"),
            anchor="w", justify="left",
        )
        self.intro.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 16))

        self.field(1, "In-game name")
        self.name = ctk.CTkEntry(self.body, width=280, placeholder_text="Minhaka")
        self.name.grid(row=1, column=1, sticky="ew")

        # Each engine has its own field, and Save keeps both: switching engines back and
        # forth doesn't lose the other one's.
        self.field(self.IPA_ROW, "IPA pronunciation")
        self.pronunciation = ctk.CTkEntry(self.body, width=280, placeholder_text="miˈɲaka")
        self.pronunciation.grid(row=self.IPA_ROW, column=1, sticky="ew")

        self.field(self.RESPELLING_ROW, "Spelled as it sounds")
        self.respelling = ctk.CTkEntry(self.body, width=280, placeholder_text="Mee-nyah-ka")
        self.respelling.grid(row=self.RESPELLING_ROW, column=1, sticky="ew")

        for entry, value in zip((self.name, self.pronunciation, self.respelling), load_name_lexicon()):
            if value:
                entry.insert(0, value)

        buttons = ctk.CTkFrame(self.body, fg_color="transparent")
        buttons.grid(row=4, column=0, columnspan=2, pady=(28, 0))
        ctk.CTkButton(
            buttons, text="Preview", command=self.preview, height=38, width=110,
            fg_color="transparent", border_width=1, text_color=MUTED_TEXT,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkButton(buttons, text="Save", command=self.save, height=38, width=130).pack(side="left")
        self.on_show()

    def on_show(self):
        self.qwen = is_qwen(saved_engine())
        self.intro.configure(text=self.INTRO["qwen3" if self.qwen else "kokoro"])
        self.show_row(self.IPA_ROW, not self.qwen)
        self.show_row(self.RESPELLING_ROW, self.qwen)

    def preview(self):
        name = self.name.get().strip()
        if not name:
            messagebox.showerror("Missing value", "Type your in-game name first.")
            return
        if self.qwen:
            # Plain text: the respelling is what it reads.
            spoken = self.respelling.get().strip() or name
        else:
            pronunciation = self.pronunciation.get().strip()
            # misaki reads "[word](/phonemes/)" as "say word with these phonemes", so
            # this plays the pronunciation as typed, before it's saved. With none typed,
            # it plays how the name is said today.
            spoken = f"[{name}](/{pronunciation}/)" if pronunciation else name
        if speech_ready(self.service):
            self.service.say(f"Well met, {spoken}.")

    def save(self):
        name = self.name.get().strip()
        pronunciation = self.pronunciation.get().strip()
        respelling = self.respelling.get().strip()
        shown = respelling if self.qwen else pronunciation
        if not name or not shown:
            messagebox.showerror(
                "Missing value",
                f"Both name and {'spelling' if self.qwen else 'pronunciation'} are required.",
            )
            return
        os.makedirs(os.path.dirname(NAME_LEXICON_PATH), exist_ok=True)
        with open(NAME_LEXICON_PATH, "w", encoding="utf-8") as f:
            f.write(build_name_lexicon(name, pronunciation, respelling))
        self.service.refresh_settings()
        messagebox.showinfo("Saved", "Name pronunciation saved.")


class VoiceRow:
    """One character's row. Edits are written straight back into its record."""

    def __init__(self, parent, record, on_remove, on_preview):
        self.record = record
        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.frame.pack(fill="x", pady=3)

        self.person = ctk.CTkEntry(self.frame, width=190, placeholder_text="Character name")
        self.person.insert(0, record["person"])
        self.person.pack(side="left", padx=(0, 8))

        self.voice = ctk.CTkComboBox(self.frame, values=VOICE_CHOICES, width=210)
        self.voice.set(voice_data.describe_voice(record["voice"]))
        self.voice.pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            self.frame, text="▶", width=32, command=lambda: on_preview(self),
            fg_color="transparent", border_width=1, text_color=MUTED_TEXT,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            self.frame, text="✕", width=32, command=lambda: on_remove(self),
            fg_color="transparent", border_width=1, text_color=MUTED_TEXT,
            hover_color=("#e5c5c5", "#5a3535"),
        ).pack(side="left")

    def commit(self):
        self.record["person"] = self.person.get()
        self.record["voice"] = voice_data.voice_from_description(self.voice.get())

    def destroy(self):
        self.frame.destroy()


class VoicesScreen(Screen):
    title = "Voice Assignments"
    subtitle = "Pick an expansion to find a character. Voices are graded A (best) to F."

    ALL = "All expansions"

    def __init__(self, parent, service):
        super().__init__(parent, service)
        self.rows = []
        try:
            self.records = load_records()
        except (OSError, ValueError) as exc:
            self.records = []
            messagebox.showerror("Could not read voices.json", str(exc))

        picker = ctk.CTkFrame(self.body, fg_color="transparent")
        picker.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ctk.CTkLabel(picker, text="Expansion").pack(side="left", padx=(0, 10))
        self.expansion = ctk.CTkOptionMenu(
            picker, values=[self.ALL] + voice_data.EXPANSIONS, width=200,
            command=lambda _: self.rebuild(),
        )
        self.expansion.set(voice_data.EXPANSIONS[0])
        self.expansion.pack(side="left")
        self.count = ctk.CTkLabel(picker, text="", text_color=("gray45", "gray60"))
        self.count.pack(side="left", padx=(12, 0))

        self.rows_frame = ctk.CTkScrollableFrame(self.body, fg_color=("gray92", "gray17"))
        self.rows_frame.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self.body.grid_rowconfigure(1, weight=1)
        self.body.grid_columnconfigure(0, weight=1)

        buttons = ctk.CTkFrame(self.body, fg_color="transparent")
        buttons.grid(row=2, column=0, columnspan=2, sticky="w", pady=(16, 0))
        ctk.CTkButton(
            buttons, text="+ Add character", command=self.add_character, height=38, width=150,
            fg_color="transparent", border_width=1, text_color=MUTED_TEXT,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkButton(buttons, text="Save", command=self.save, height=38, width=130).pack(side="left")

        self.rebuild()

    def commit_rows(self):
        """Fold visible edits back into the records before the rows are discarded."""
        for row in self.rows:
            row.commit()

    def visible_records(self):
        selected = self.expansion.get()
        if selected == self.ALL:
            return self.records
        return [r for r in self.records if r["expansion"] == selected]

    def rebuild(self):
        self.commit_rows()
        for row in self.rows:
            row.destroy()
        # Only the selected expansion's rows exist as widgets; building all ~110 at
        # once is slow enough to be visible when switching screens.
        self.rows = [
            VoiceRow(self.rows_frame, record, self.remove_row, self.preview_row)
            for record in self.visible_records()
        ]
        shown, total = len(self.rows), len(self.records)
        self.count.configure(text=f"{shown} of {total} characters")

    def add_character(self):
        selected = self.expansion.get()
        expansion = "Custom" if selected == self.ALL else selected
        self.commit_rows()
        self.records.append({"expansion": expansion, "person": "", "voice": "af_heart"})
        if selected != expansion:
            self.expansion.set(expansion)
        self.rebuild()

    def preview_row(self, row):
        # The row as it is on screen, saved or not. The character introduces themself,
        # which also tries how their name is pronounced.
        if not speech_ready(self.service):
            return
        # voices.json keeps names lowercase; capitalize them as they appear in game.
        name = " ".join(word[:1].upper() + word[1:] for word in row.person.get().split())
        self.service.say(
            f"Well met, adventurer. I am {name}." if name else "Well met, adventurer.",
            voice_data.voice_from_description(row.voice.get()),
            person=name,
        )

    def remove_row(self, row):
        self.records.remove(row.record)
        self.rows.remove(row)
        row.destroy()
        self.count.configure(text=f"{len(self.rows)} of {len(self.records)} characters")

    def save(self):
        self.commit_rows()
        save_records(self.records)
        self.service.refresh_settings()
        messagebox.showinfo("Saved", "Voice assignments saved.")


class QwenVoicesScreen(Screen):
    """What the Voices button opens under Qwen3: each character's voice is designed
    from a description, rather than picked from Kokoro's."""

    title = "Voices"
    subtitle = ("Pick a name to see the description its voice was made from. Change it, press\n"
                "Design to hear a new voice, and Keep the one you like. Grayed names have no\n"
                "voice of their own yet, so they get one by their race or name.")

    # Characters that can't go in a file name, which is what a voice is saved as.
    UNSAFE = set('/\\:*?"<>|')
    DEFAULTS = "Defaults"
    RACES = "Races"
    OTHER = "Other names"
    # {clip name: (race or age, gender)} for every race voice.
    RACE_CLIPS = {
        name: (race, gender)
        for race in voice_data.RACES + voice_data.AGES
        for gender in qwen_engine.GENDER_VOICES
        for name in qwen_engine.race_voices(race, gender)
    }
    # Who a race voice speaks for, by race or age and gender.
    AGE_PEOPLE = {
        ("youth", "female"): "girls", ("youth", "male"): "boys",
        ("elder", "female"): "old women", ("elder", "male"): "old men",
    }
    # The editor's width, which its labels wrap to.
    WRAP = 360

    def __init__(self, parent, service):
        super().__init__(parent, service)
        self.designs = {}
        self.reference_text = ""
        # {lowercased name: name as its file is called} for every voice with a clip.
        self.clips = {}
        # {character: the gender of their Kokoro voice}, from voices.json, which decides
        # the default they fall back on.
        self.genders = {}
        # {group: [names]}: the defaults, the characters of each expansion, then voices
        # for names voices.json doesn't have.
        self.groups = {}
        # Names New voice added, until Keep saves a voice for them.
        self.added = set()
        self.selected = None
        self.name_buttons = {}
        # The VoiceDesign model, loaded at the first Design and released on leaving this
        # screen: it holds 2 GB of the GPU, which the game wants back.
        self.designer = None
        self.busy = False
        self.release_pending = False
        # The voice Design made last, not kept yet: {"name", "instruct", "seed", "samples"}.
        self.candidate = None
        self.results = queue.Queue()

        self.group = ctk.CTkOptionMenu(self.body, values=[], width=200, command=lambda _: self.show_group())
        self.group.grid(row=0, column=0, sticky="w")
        self.names = ctk.CTkScrollableFrame(self.body, width=176, fg_color=("gray92", "gray17"))
        self.names.grid(row=1, column=0, sticky="ns", pady=(10, 0))
        self.body.grid_rowconfigure(1, weight=1)
        ctk.CTkButton(
            self.body, text="+ New voice", command=self.new_voice, height=34, width=200,
            fg_color="transparent", border_width=1, text_color=MUTED_TEXT,
        ).grid(row=2, column=0, sticky="w", pady=(10, 0))

        editor = ctk.CTkFrame(self.body, fg_color="transparent")
        editor.grid(row=0, column=1, rowspan=3, sticky="nsew", padx=(20, 0))
        editor.grid_columnconfigure(0, weight=1)
        self.heading = ctk.CTkLabel(editor, text="", font=ctk.CTkFont(size=17, weight="bold"), anchor="w")
        self.heading.grid(row=0, column=0, sticky="w")
        self.about = ctk.CTkLabel(
            editor, text="", font=ctk.CTkFont(size=11), text_color=("gray45", "gray60"),
            anchor="w", justify="left", wraplength=self.WRAP,
        )
        self.about.grid(row=1, column=0, sticky="w", pady=(2, 14))

        ctk.CTkLabel(editor, text="Description", anchor="w").grid(row=2, column=0, sticky="w")
        self.description = ctk.CTkTextbox(editor, height=120, wrap="word")
        self.description.grid(row=3, column=0, sticky="ew", pady=(4, 4))
        ctk.CTkLabel(
            editor,
            text="Who they are and how they sound: gender, age, pitch, accent, personality. "
                 'e.g. "An old man with a deep, gravelly British voice. Slow, wise and kind."',
            font=ctk.CTkFont(size=11), text_color=("gray45", "gray60"),
            anchor="w", justify="left", wraplength=self.WRAP,
        ).grid(row=4, column=0, sticky="w")

        buttons = ctk.CTkFrame(editor, fg_color="transparent")
        buttons.grid(row=5, column=0, sticky="w", pady=(20, 0))
        self.design_button = ctk.CTkButton(buttons, text="Design", command=self.design, height=38, width=84)
        self.design_button.pack(side="left", padx=(0, 6))
        self.play_new_button = ctk.CTkButton(
            buttons, text="Play new", command=self.play_new, height=38, width=84,
            fg_color="transparent", border_width=1, text_color=MUTED_TEXT,
        )
        self.play_new_button.pack(side="left", padx=(0, 6))
        self.play_current_button = ctk.CTkButton(
            buttons, text="Play current", command=self.play_current, height=38, width=96,
            fg_color="transparent", border_width=1, text_color=MUTED_TEXT,
        )
        self.play_current_button.pack(side="left", padx=(0, 6))
        self.keep_button = ctk.CTkButton(buttons, text="Keep", command=self.keep, height=38, width=84)
        self.keep_button.pack(side="left")

        self.status = ctk.CTkLabel(editor, text="", anchor="w", justify="left", wraplength=self.WRAP)
        self.status.grid(row=6, column=0, sticky="w", pady=(16, 0))

        self.reload()

    def on_show(self):
        # Only the lists: the description may hold edits that aren't designed yet.
        self.reload()

    def on_hide(self):
        if self.busy:
            self.release_pending = True
        else:
            self._release()

    def _release(self):
        self.release_pending = False
        if self.designer is not None:
            self.designer.close()
            self.designer = None

    def reload(self):
        """Read the voices again: Keep, the Kokoro Voices screen or tools/design_voices.py
        may have changed them."""
        data = qwen_engine.load_designs()
        self.designs, self.reference_text = data["voices"], data["reference_text"]
        self.clips = {name.lower(): name for name in qwen_engine.clip_names()}
        try:
            characters = voice_data.load(VOICES_PATH)
        except (OSError, ValueError):
            characters = {}
        self.genders = {
            person.lower(): voice_data.classify_gender(voice)
            for people in characters.values() for person, voice in people.items()
        }

        groups = {self.DEFAULTS: sorted(DEFAULT_GENDERS), self.RACES: list(self.RACE_CLIPS)}
        for expansion in sorted(characters, key=lambda e: (
            voice_data.EXPANSIONS.index(e) if e in voice_data.EXPANSIONS else len(voice_data.EXPANSIONS), e
        )):
            groups[expansion] = sorted(person.lower() for person in characters[expansion])
        # The clips named after Kokoro voices speak for names with no voice of their own
        # when the game doesn't say their race (qwen_engine.NAMED_VOICES): they aren't a
        # name's own, so they aren't listed.
        others = (set(self.designs) | set(self.clips) | self.added) - set(self.genders)
        groups[self.OTHER] = sorted(
            others - set(DEFAULT_GENDERS) - set(self.RACE_CLIPS) - set(voice_data.VOICE_GRADES)
        )
        self.groups = {group: names for group, names in groups.items() if names}

        self.group.configure(values=list(self.groups))
        if self.group.get() not in self.groups:
            self.group.set(self.DEFAULTS)
        self.show_group()
        if self.selected is None:
            self.select(self.groups[self.DEFAULTS][0])
        else:
            self._describe()

    def show_group(self):
        """List the names in the group picked. The voice being edited stays on screen."""
        for button in self.name_buttons.values():
            button.destroy()
        self.name_buttons = {}
        for name in self.groups.get(self.group.get(), []):
            button = ctk.CTkButton(
                self.names, text=self.display(name), anchor="w", height=28, corner_radius=6,
                fg_color="transparent", hover_color=("gray80", "gray30"),
                command=lambda n=name: self.select(n),
            )
            button.pack(fill="x", pady=1)
            self.name_buttons[name] = button
        self._mark()

    def _mark(self):
        """Highlight the voice being edited, and gray the names with no voice of their own."""
        for name, button in self.name_buttons.items():
            button.configure(
                fg_color=("gray75", "gray25") if name == self.selected else "transparent",
                text_color=MUTED_TEXT if name in self.clips else ("gray55", "gray50"),
            )

    def display(self, name):
        if name in DEFAULT_GENDERS:
            return name.capitalize()
        # voices.json keeps names lowercase; capitalize them as they appear in game.
        return " ".join(word[:1].upper() + word[1:] for word in name.split())

    def select(self, name):
        if name == self.selected:
            return
        if self.busy:
            self._say("Wait for the voice being designed to finish first.")
            return
        self.selected = name
        # Keep only ever saves what was designed for the voice on screen.
        self.candidate = None
        self.description.delete("1.0", "end")
        self.description.insert("1.0", self.designs.get(name, {}).get("instruct", ""))
        self.heading.configure(text=self.display(name))
        self._describe()
        self._say("")
        self._mark()
        self._update_buttons()

    def _fallback(self, name):
        """The clip a name speaks with while it has no voice of its own and the game
        doesn't say their race: None for a name voices.json doesn't have, whose gender
        the game reports line by line."""
        gender = self.genders.get(name)
        return voice_data.voice_by_name(name, qwen_engine.NAMED_VOICES[gender]) if gender else None

    def _describe(self):
        name = self.selected
        if name in DEFAULT_GENDERS:
            text = (f"Speaks for every {DEFAULT_GENDERS[name]} line that comes without a name "
                    "or a race.")
        elif name in self.RACE_CLIPS:
            race, gender = self.RACE_CLIPS[name]
            people = self.AGE_PEOPLE.get((race, gender)) or f"{gender} {self.display(race)}"
            text = (f"Speaks for some of the {people} without a voice of their own, each "
                    "picked by their name.")
            if name not in self.clips:
                text += " It has no voice yet: describe one and press Design."
        elif name in self.clips:
            text = "Has a voice of their own."
        else:
            text = ("No voice of their own yet. They speak with one of their race's voices, or "
                    "one picked by their name when the game doesn't say their race.")
        self.about.configure(text=text)

    def _update_buttons(self):
        self.design_button.configure(state="disabled" if self.busy else "normal")
        state = "normal" if self.candidate and not self.busy else "disabled"
        self.play_new_button.configure(state=state)
        self.keep_button.configure(state=state)

    def _say(self, text, error=False):
        self.status.configure(text=text, text_color="#e04f4f" if error else ("gray30", "gray70"))

    def _play(self, samples):
        try:
            audio_output.play_samples(samples, qwen_engine.SAMPLE_RATE, load_settings().get("audio_device", "auto"))
        except Exception as exc:
            self._say(f"Could not play it: {exc}", error=True)

    def new_voice(self):
        if self.busy:
            self._say("Wait for the voice being designed to finish first.")
            return
        dialog = ctk.CTkInputDialog(title="New voice", text="The character's name, as the game shows it:")
        name = (dialog.get_input() or "").strip().lower()
        if name:
            self.add_voice(name)

    def add_voice(self, name):
        if self.UNSAFE & set(name):
            messagebox.showerror("Name", 'A name can\'t contain any of / \\ : * ? " < > |')
            return
        if not any(name in names for names in self.groups.values()):
            self.added.add(name)
            self.reload()
        self.group.set(next(group for group, names in self.groups.items() if name in names))
        self.show_group()
        self.select(name)
        self._say("Describe their voice, then press Design.")

    def design(self):
        name = self.selected
        instruct = self.description.get("1.0", "end").strip()
        if not instruct:
            messagebox.showerror("Missing value", "Describe the voice first.")
            return
        self.busy = True
        self.candidate = None
        self._update_buttons()
        if self.designer is None:
            self._say("Loading the voice designer. The first time, it downloads 2 GB: the Run "
                      "screen shows how far it got.")
        else:
            self._say(f"Designing a voice for {self.display(name)}...")
        # A new seed each time, so Design again gives another voice for the same words.
        seed = random.randrange(1, 2 ** 31)
        threading.Thread(target=self._design, args=(name, instruct, seed), daemon=True).start()
        self.after(150, self._poll)

    def _design(self, name, instruct, seed):
        # Off the UI thread: loading takes seconds, and the first time a 2 GB download.
        try:
            if self.designer is None:
                self.designer = qwen_engine.Designer()
            started = time.monotonic()
            samples = self.designer.design(instruct, self.reference_text, seed)
            self.results.put({"name": name, "instruct": instruct, "seed": seed, "samples": samples,
                              "took": time.monotonic() - started})
        except Exception as exc:
            self.results.put({"error": str(exc)})

    def _poll(self):
        """Tk isn't thread-safe, so the designing thread hands its result over here."""
        try:
            result = self.results.get_nowait()
        except queue.Empty:
            self.after(150, self._poll)
            return
        self.busy = False
        if "error" in result:
            self._say(f"Could not design the voice: {result['error']}", error=True)
        else:
            took = result.pop("took")
            self.candidate = result
            self._say(f"A new voice for {self.display(result['name'])}, made in {took:.1f} s. "
                      "Keep it, or press Design again for another one.")
            self._play(result["samples"])
        if self.release_pending:
            self._release()
        self._update_buttons()

    def play_new(self):
        if self.candidate:
            self._play(self.candidate["samples"])

    def play_current(self):
        """The voice the selected name speaks with now: their own, else the one picked by
        their name for when the game doesn't say their race."""
        for name in (self.selected, self._fallback(self.selected)):
            if name in self.clips:
                self._play(qwen_engine.read_wav(
                    os.path.join(qwen_engine.voices_dir(), f"{self.clips[name]}.wav")))
                return
        self._say(f"{self.display(self.selected)} has no voice yet. Describe one and press Design.")

    def keep(self):
        candidate = self.candidate
        if not candidate:
            return
        name = candidate["name"]
        qwen_engine.write_clip(name, candidate["samples"], self.reference_text)
        qwen_engine.save_design(name, candidate["instruct"], candidate["seed"])
        self.service.reload_voice(name)
        self.candidate = None
        self.added.discard(name)
        self.reload()
        self._update_buttons()
        if self.service.qwen is not None:
            self._say(f"Kept. {self.display(name)} speaks with it from the next line.")
        else:
            self._say(f"Kept. {self.display(name)} speaks with it once the TTS starts.")


class SettingsApp(ctk.CTk):
    SCREENS = (
        ("Run", RunScreen),
        ("General", GeneralScreen),
        ("Tuning", VoiceTuningScreen),
        ("Name", NameScreen),
        ("Voices", VoicesScreen),
        ("Audio", AudioScreen),
    )
    # Under Qwen3 a character's voice is designed rather than picked from Kokoro's, so
    # these buttons open another screen.
    QWEN_SCREENS = {"Voices": QwenVoicesScreen}
    # Hidden from the sidebar under Qwen3: Tuning's overrides are per Kokoro voice.
    KOKORO_ONLY = ("Tuning",)

    def __init__(self):
        super().__init__()
        self.title("FFXIV TTS")
        self.geometry("820x620")
        self.minsize(700, 540)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.service = tts.TTSService(on_state_change=self.on_service_state)

        sidebar = ctk.CTkFrame(self, width=170, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_rowconfigure(len(self.SCREENS) + 1, weight=1)
        sidebar.grid_propagate(False)

        ctk.CTkLabel(
            sidebar, text="FFXIV TTS", font=ctk.CTkFont(size=17, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(24, 20))

        self.screens = {}
        self.nav_buttons = {}
        self.current_screen = None
        for i, (name, screen_class) in enumerate(self.SCREENS, start=1):
            button = ctk.CTkButton(
                sidebar, text=name, anchor="w", height=38, corner_radius=8,
                text_color=MUTED_TEXT, command=lambda n=name: self.show(n),
            )
            button.grid(row=i, column=0, padx=12, pady=3, sticky="ew")
            self.nav_buttons[name] = button

            screen = screen_class(self, self.service)
            screen.grid(row=0, column=1, sticky="nsew")
            self.screens[name] = screen
        self.qwen_screens = {}
        for name, screen_class in self.QWEN_SCREENS.items():
            screen = screen_class(self, self.service)
            screen.grid(row=0, column=1, sticky="nsew")
            self.qwen_screens[name] = screen

        ctk.CTkLabel(sidebar, text="Appearance", font=ctk.CTkFont(size=11), anchor="w").grid(
            row=len(self.SCREENS) + 2, column=0, padx=20, sticky="w"
        )
        # A segmented button rather than an option menu: this sits at the bottom of
        # the sidebar, and a dropdown would open below the window, off-screen.
        appearance = ctk.CTkSegmentedButton(
            sidebar, values=["Dark", "Light", "System"], width=146,
            text_color=MUTED_TEXT, command=self.set_appearance,
        )
        appearance.set(load_settings().get("appearance", "Dark"))
        appearance.grid(row=len(self.SCREENS) + 3, column=0, padx=12, pady=(4, 20))

        # Everything the runtime prints goes to the Run screen's log.
        run_screen = self.screens["Run"]
        sys.stdout = LogStream(run_screen.append, sys.stdout)
        sys.stderr = LogStream(run_screen.append, sys.stderr)

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.apply_engine()
        self.show(self.SCREENS[0][0])

    def apply_engine(self):
        """Show the screens for the engine General has saved."""
        qwen = is_qwen(saved_engine())
        for name in self.KOKORO_ONLY:
            # Rows keep their place in the sidebar's grid, so it comes back where it was.
            if qwen:
                self.nav_buttons[name].grid_remove()
            else:
                self.nav_buttons[name].grid()

    def screen_for(self, name):
        if name in self.qwen_screens and is_qwen(saved_engine()):
            return self.qwen_screens[name]
        return self.screens[name]

    def on_service_state(self, running):
        # Called from the service thread; hop back to the UI thread to touch widgets.
        self.after(0, self.screens["Run"].show_state)

    def on_close(self):
        self.service.stop()
        self.destroy()

    def set_appearance(self, mode):
        ctk.set_appearance_mode(mode)
        update_settings(appearance=mode)

    def show(self, name):
        screen = self.screen_for(name)
        if self.current_screen not in (None, screen):
            self.current_screen.on_hide()
        self.current_screen = screen
        # Screens are built once and kept, so let the one coming up pick up anything
        # another screen saved in the meantime.
        screen.on_show()
        screen.tkraise()
        for screen_name, button in self.nav_buttons.items():
            button.configure(
                fg_color=("gray75", "gray25") if screen_name == name else "transparent"
            )


def run():
    ctk.set_appearance_mode(load_settings().get("appearance", "Dark"))
    ctk.set_default_color_theme("blue")
    SettingsApp().mainloop()


if __name__ == "__main__":
    run()

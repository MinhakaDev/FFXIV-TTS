import json
import os
import queue
import sys
import tkinter as tk
import webbrowser
from tkinter import messagebox

import customtkinter as ctk

import audio as audio_output
import paths
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

DEFAULT_SETTINGS = {
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


def build_name_lexicon(name, pronunciation):
    name_upper = name[0].upper() + name[1:]
    name_lower = name[0].lower() + name[1:]
    return f"""<?xml version='1.0' encoding='UTF-8'?>
<lexicon xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" version="1.0"
    xsi:schemaLocation="http://www.w3.org/2005/01/pronunciation-lexicon http://www.w3.org/TR/2007/CR-pronunciation-lexicon-20071212/pls.xsd"
    alphabet="ipa" xml:lang="en" xmlns="http://www.w3.org/2005/01/pronunciation-lexicon">
    <lexeme>
        <grapheme>{name_lower}</grapheme>
        <grapheme>{name_upper}</grapheme>
        <phoneme>{pronunciation}</phoneme>
    </lexeme>
</lexicon>"""


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


class Screen(ctk.CTkFrame):
    """A page in the sidebar, with a title and a consistent content column."""

    title = ""
    subtitle = ""

    def __init__(self, parent):
        super().__init__(parent, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 0))
        ctk.CTkLabel(
            header, text=self.title, font=ctk.CTkFont(size=22, weight="bold"), anchor="w"
        ).pack(anchor="w")
        if self.subtitle:
            ctk.CTkLabel(
                header, text=self.subtitle, font=ctk.CTkFont(size=12),
                text_color=("gray45", "gray60"), anchor="w", justify="left",
            ).pack(anchor="w", pady=(2, 0))

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=28, pady=18)
        self.body.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

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


class RunScreen(Screen):
    title = "FFXIV TTS"
    subtitle = "Start FFXIV and log in, then press Start."

    def __init__(self, parent, service):
        super().__init__(parent)
        self.service = service
        self.messages = queue.Queue()

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

        self.button = ctk.CTkButton(
            self.body, text="Start", command=self.toggle, height=42, width=150,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.button.grid(row=1, column=1, sticky="e")

        self.log = ctk.CTkTextbox(
            self.body, fg_color=("gray92", "gray14"), font=ctk.CTkFont(family="monospace", size=11),
            wrap="word",
        )
        self.log.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(16, 0))
        self.log.configure(state="disabled")
        self.body.grid_rowconfigure(2, weight=1)
        self.body.grid_columnconfigure(0, weight=1)

        self.append("Press Start to begin. The speech model loads on the first run,")
        self.append("which can take a minute and needs an internet connection.")
        self.drain()

        # Check GitHub for a newer build off the UI thread; check() no-ops on a dev build.
        updates.check_async(lambda result: self.after(0, lambda: self.show_update(result)))

    def show_update(self, result):
        if not result:
            return
        tag, url = result
        self.update_url = url
        self.banner_label.configure(text=f"Update available: {tag}. You have {updates.__version__}.")
        self.banner.grid()
        self.append(f"Update available: {tag} (you have {updates.__version__}). Press Download to get it.")

    def open_download(self):
        webbrowser.open(self.update_url or updates.RELEASES_PAGE)

    def append(self, line):
        self.messages.put(line)

    def drain(self):
        """Tk isn't thread-safe, so log lines are queued and flushed on the UI thread."""
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
        self.after(150, self.drain)

    def toggle(self):
        if self.service.running:
            self.append("Stopping...")
            self.service.stop()
        else:
            self.service.start()

    def set_running(self, running):
        self.status.configure(text="Running" if running else "Stopped")
        self.button.configure(text="Stop" if running else "Start")


class GeneralScreen(Screen):
    title = "General"
    subtitle = "Language and how loud each voice speaks."

    def __init__(self, parent):
        super().__init__(parent)
        settings = load_settings()

        self.field(0, "Region")
        self.region = ctk.CTkSegmentedButton(
            self.body, values=["US", "UK"], width=200, text_color=MUTED_TEXT,
        )
        self.region.set(settings.get("region", "US"))
        self.region.grid(row=0, column=1, sticky="w")

        self.speed = self.slider(1, "Speech speed", settings.get("speed", 1.0), 0.5, 2.0)
        self.male_volume = self.slider(2, "Male volume", settings.get("malevolume", 0.7), 0.0, 2.0)
        self.female_volume = self.slider(3, "Female volume", settings.get("femalevolume", 0.7), 0.0, 2.0)

        self.save_button(4, self.save)

    def save(self):
        update_settings(
            region=self.region.get(),
            speed=round(self.speed.get(), 2),
            malevolume=round(self.male_volume.get(), 2),
            femalevolume=round(self.female_volume.get(), 2),
        )
        messagebox.showinfo("Saved", "General settings saved.")


class VoiceTuningScreen(Screen):
    title = "Voice Tuning"
    subtitle = "Override volume and speed for one voice at a time.\nVoices you leave alone use the General defaults."

    def __init__(self, parent):
        super().__init__(parent)
        settings = load_settings()
        self.male_default = settings.get("malevolume", 0.7)
        self.female_default = settings.get("femalevolume", 0.7)
        self.speed_default = settings.get("speed", 1.0)
        self.overrides = {v: dict(o) for v, o in settings.get("voice_settings", {}).items()}
        self.current = None

        self.field(0, "Voice")
        self.voice = ctk.CTkOptionMenu(
            self.body, values=VOICE_CHOICES, width=260,
            command=lambda _: self.load_voice(),
        )
        self.voice.set(VOICE_CHOICES[0])
        self.voice.grid(row=0, column=1, sticky="w")

        self.volume = self.slider(1, "Volume", self.female_default, 0.0, 2.0)
        self.speed = self.slider(2, "Speed", self.speed_default, 0.5, 2.0)

        buttons = ctk.CTkFrame(self.body, fg_color="transparent")
        buttons.grid(row=3, column=0, columnspan=2, pady=(28, 0))
        ctk.CTkButton(
            buttons, text="Reset to default", command=self.reset, height=38, width=150,
            fg_color="transparent", border_width=1, text_color=MUTED_TEXT,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkButton(buttons, text="Save", command=self.save, height=38, width=130).pack(side="left")

        self.load_voice()

    def _defaults_for(self, voice):
        volume = self.female_default if voice_data.classify_gender(voice) == "female" else self.male_default
        return volume, self.speed_default

    def load_voice(self):
        # Commit the voice we were showing before moving to the new one, so edits
        # survive switching between voices without a save in between.
        if self.current is not None:
            self.overrides[self.current] = {
                "volume": round(self.volume.get(), 2),
                "speed": round(self.speed.get(), 2),
            }
        voice = voice_data.voice_from_description(self.voice.get())
        self.current = voice
        override = self.overrides.get(voice)
        if override:
            volume, speed = override["volume"], override["speed"]
        else:
            volume, speed = self._defaults_for(voice)
        self.volume.set(volume)
        self.speed.set(speed)

    def reset(self):
        voice = voice_data.voice_from_description(self.voice.get())
        self.overrides.pop(voice, None)
        volume, speed = self._defaults_for(voice)
        self.volume.set(volume)
        self.speed.set(speed)

    def save(self):
        self.load_voice()  # fold the on-screen voice back into overrides
        # Drop anything left at the defaults so tweaking then reverting a voice
        # doesn't freeze it against later changes to the General defaults.
        self.overrides = {
            voice: {"volume": round(o["volume"], 2), "speed": round(o["speed"], 2)}
            for voice, o in self.overrides.items()
            if (round(o["volume"], 2), round(o["speed"], 2))
            != tuple(round(d, 2) for d in self._defaults_for(voice))
        }
        update_settings(voice_settings=self.overrides)
        messagebox.showinfo("Saved", "Voice tuning saved.")


class AudioScreen(Screen):
    title = "Audio"
    subtitle = "Where the speech is played. Use Test sound to check it before saving."

    def __init__(self, parent):
        super().__init__(parent)
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
        update_settings(audio_device=self.device.get().strip() or "auto")
        messagebox.showinfo("Saved", "Audio device saved.")


class NameScreen(Screen):
    title = "Name Pronunciation"
    subtitle = "Teach the TTS how to say your character's name."

    def __init__(self, parent):
        super().__init__(parent)

        ctk.CTkLabel(
            self.body,
            text="Ask ChatGPT how to pronounce your name in the IPA alphabet,\n"
                 'e.g. "Minhaka" becomes "miˈɲaka". Test it at ipa-reader.com.',
            font=ctk.CTkFont(size=12), text_color=("gray45", "gray60"),
            anchor="w", justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 16))

        self.field(1, "In-game name")
        self.name = ctk.CTkEntry(self.body, width=280, placeholder_text="Minhaka")
        self.name.grid(row=1, column=1, sticky="ew")

        self.field(2, "IPA pronunciation")
        self.pronunciation = ctk.CTkEntry(self.body, width=280, placeholder_text="miˈɲaka")
        self.pronunciation.grid(row=2, column=1, sticky="ew")

        self.save_button(3, self.save)

    def save(self):
        name = self.name.get().strip()
        pronunciation = self.pronunciation.get().strip()
        if not name or not pronunciation:
            messagebox.showerror("Missing value", "Both name and pronunciation are required.")
            return
        os.makedirs(os.path.dirname(NAME_LEXICON_PATH), exist_ok=True)
        with open(NAME_LEXICON_PATH, "w", encoding="utf-8") as f:
            f.write(build_name_lexicon(name, pronunciation))
        messagebox.showinfo("Saved", "Name pronunciation saved.")


class VoiceRow:
    """One character's row. Edits are written straight back into its record."""

    def __init__(self, parent, record, on_remove):
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

    def __init__(self, parent):
        super().__init__(parent)
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
            VoiceRow(self.rows_frame, record, self.remove_row)
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

    def remove_row(self, row):
        self.records.remove(row.record)
        self.rows.remove(row)
        row.destroy()
        self.count.configure(text=f"{len(self.rows)} of {len(self.records)} characters")

    def save(self):
        self.commit_rows()
        save_records(self.records)
        messagebox.showinfo("Saved", "Voice assignments saved.")


class SettingsApp(ctk.CTk):
    SCREENS = (
        ("Run", RunScreen),
        ("General", GeneralScreen),
        ("Tuning", VoiceTuningScreen),
        ("Name", NameScreen),
        ("Voices", VoicesScreen),
        ("Audio", AudioScreen),
    )

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
        for i, (name, screen_class) in enumerate(self.SCREENS, start=1):
            button = ctk.CTkButton(
                sidebar, text=name, anchor="w", height=38, corner_radius=8,
                text_color=MUTED_TEXT, command=lambda n=name: self.show(n),
            )
            button.grid(row=i, column=0, padx=12, pady=3, sticky="ew")
            self.nav_buttons[name] = button

            screen = screen_class(self, self.service) if screen_class is RunScreen else screen_class(self)
            screen.grid(row=0, column=1, sticky="nsew")
            self.screens[name] = screen

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
        self.show(self.SCREENS[0][0])

    def on_service_state(self, running):
        # Called from the service thread; hop back to the UI thread to touch widgets.
        self.after(0, lambda: self.screens["Run"].set_running(running))

    def on_close(self):
        self.service.stop()
        self.destroy()

    def set_appearance(self, mode):
        ctk.set_appearance_mode(mode)
        update_settings(appearance=mode)

    def show(self, name):
        self.screens[name].tkraise()
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

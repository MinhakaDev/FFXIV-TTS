import json
import os
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

import audio as audio_output
import paths

KNOWN_VOICES = {
    "female": [
        "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore",
        "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
        "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    ],
    "male": [
        "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael",
        "am_onyx", "am_puck", "am_santa",
        "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
    ],
}
ALL_VOICES = sorted(KNOWN_VOICES["female"] + KNOWN_VOICES["male"])

SETTINGS_PATH = os.path.join(paths.settings_dir(), "settings.json")
VOICES_PATH = os.path.join(paths.settings_dir(), "voices.json")
NAME_LEXICON_PATH = os.path.join(paths.lexicons_dir(), "Your-Name", "lexicon.pls")

DEFAULT_SETTINGS = {
    "region": "US",
    "speed": 1.0,
    "malevolume": 0.7,
    "femalevolume": 0.7,
    "audio_device": "auto",
    "appearance": "Dark",
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


def load_voices():
    with open(VOICES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_voices(voices):
    with open(VOICES_PATH, "w", encoding="utf-8") as f:
        json.dump(voices, f, indent=2)


def flatten_voices(voices):
    rows = []
    for gender_data in voices.values():
        for voice, people in gender_data.items():
            for person in people:
                rows.append((person, voice))
    rows.sort(key=lambda row: row[0].lower())
    return rows


def classify_gender(voice):
    return "female" if voice.startswith(("af_", "bf_")) else "male"


def regroup_voices(rows):
    voices = {"male": {}, "female": {}}
    for person, voice in rows:
        person = person.strip()
        voice = voice.strip()
        if not person or not voice:
            continue
        voices[classify_gender(voice)].setdefault(voice, []).append(person)
    return voices


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
        ctk.CTkSlider(
            holder, from_=from_, to=to, variable=var, number_of_steps=int((to - from_) * 20),
            command=lambda v: readout.configure(text=f"{float(v):.1f}"),
        ).grid(row=0, column=0, sticky="ew")
        return var

    def save_button(self, row, command, text="Save"):
        ctk.CTkButton(self.body, text=text, command=command, height=38, width=130).grid(
            row=row, column=0, columnspan=2, pady=(28, 0)
        )


class GeneralScreen(Screen):
    title = "General"
    subtitle = "Language and how loud each voice speaks."

    def __init__(self, parent):
        super().__init__(parent)
        settings = load_settings()

        self.field(0, "Region")
        self.region = ctk.CTkSegmentedButton(
            self.body, values=["US", "UK"], width=200,
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
            fg_color="transparent", border_width=1,
        ).grid(row=0, column=1, padx=(8, 0))

        ctk.CTkLabel(
            self.body,
            text='"auto" prefers pipewire/pulse over the generic ALSA default.\n'
                 "Headset missing? Switch it on, then press Refresh - it is only detected\n"
                 "if it was already awake. Wireless headsets often sleep on their own.\n"
                 "You can also pick pipewire/pulse and choose the headset in your\n"
                 "system's sound settings instead.",
            font=ctk.CTkFont(size=11), text_color=("gray45", "gray60"),
            anchor="w", justify="left",
        ).grid(row=1, column=1, sticky="w", pady=(6, 0))

        buttons = ctk.CTkFrame(self.body, fg_color="transparent")
        buttons.grid(row=2, column=0, columnspan=2, pady=(28, 0))
        ctk.CTkButton(
            buttons, text="Test sound", command=self.test, height=38, width=130,
            fg_color="transparent", border_width=1,
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
    def __init__(self, parent, on_remove, person="", voice=""):
        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.frame.pack(fill="x", pady=3)

        self.person = ctk.CTkEntry(self.frame, width=210, placeholder_text="Character name")
        self.person.insert(0, person)
        self.person.pack(side="left", padx=(0, 8))

        self.voice = ctk.CTkComboBox(self.frame, values=ALL_VOICES, width=150)
        self.voice.set(voice or ALL_VOICES[0])
        self.voice.pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            self.frame, text="✕", width=32, command=lambda: on_remove(self),
            fg_color="transparent", border_width=1, hover_color=("#e5c5c5", "#5a3535"),
        ).pack(side="left")

    def values(self):
        return self.person.get(), self.voice.get()

    def destroy(self):
        self.frame.destroy()


class VoicesScreen(Screen):
    title = "Voice Assignments"
    subtitle = "Give specific characters their own voice."

    def __init__(self, parent):
        super().__init__(parent)
        self.rows = []

        self.rows_frame = ctk.CTkScrollableFrame(self.body, fg_color=("gray92", "gray17"))
        self.rows_frame.grid(row=0, column=0, columnspan=2, sticky="nsew")
        self.body.grid_rowconfigure(0, weight=1)
        self.body.grid_columnconfigure(0, weight=1)

        buttons = ctk.CTkFrame(self.body, fg_color="transparent")
        buttons.grid(row=1, column=0, columnspan=2, sticky="w", pady=(16, 0))
        ctk.CTkButton(
            buttons, text="+ Add character", command=self.add_row, height=38, width=150,
            fg_color="transparent", border_width=1,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkButton(buttons, text="Save", command=self.save, height=38, width=130).pack(side="left")

        try:
            for person, voice in flatten_voices(load_voices()):
                self.add_row(person, voice)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not read voices.json", str(exc))

    def add_row(self, person="", voice=""):
        self.rows.append(VoiceRow(self.rows_frame, self.remove_row, person, voice))

    def remove_row(self, row):
        self.rows.remove(row)
        row.destroy()

    def save(self):
        save_voices(regroup_voices([row.values() for row in self.rows]))
        messagebox.showinfo("Saved", "Voice assignments saved.")


class SettingsApp(ctk.CTk):
    SCREENS = (
        ("General", GeneralScreen),
        ("Name", NameScreen),
        ("Voices", VoicesScreen),
        ("Audio", AudioScreen),
    )

    def __init__(self):
        super().__init__()
        self.title("FFXIV TTS Settings")
        self.geometry("780x580")
        self.minsize(680, 520)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

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
                command=lambda n=name: self.show(n),
            )
            button.grid(row=i, column=0, padx=12, pady=3, sticky="ew")
            self.nav_buttons[name] = button

            screen = screen_class(self)
            screen.grid(row=0, column=1, sticky="nsew")
            self.screens[name] = screen

        ctk.CTkLabel(sidebar, text="Appearance", font=ctk.CTkFont(size=11), anchor="w").grid(
            row=len(self.SCREENS) + 2, column=0, padx=20, sticky="w"
        )
        appearance = ctk.CTkOptionMenu(
            sidebar, values=["Dark", "Light", "System"], width=140,
            command=self.set_appearance,
        )
        appearance.set(load_settings().get("appearance", "Dark"))
        appearance.grid(row=len(self.SCREENS) + 3, column=0, padx=12, pady=(4, 20))

        self.show(self.SCREENS[0][0])

    def set_appearance(self, mode):
        ctk.set_appearance_mode(mode)
        update_settings(appearance=mode)

    def show(self, name):
        self.screens[name].tkraise()
        for screen_name, button in self.nav_buttons.items():
            button.configure(
                fg_color=("gray75", "gray25") if screen_name == name else "transparent"
            )


if __name__ == "__main__":
    ctk.set_appearance_mode(load_settings().get("appearance", "Dark"))
    ctk.set_default_color_theme("blue")
    SettingsApp().mainloop()

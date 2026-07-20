import json
import os
import tkinter as tk
from tkinter import messagebox, ttk

import sounddevice as sd

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


def load_settings():
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_settings(settings):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=4)


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
        gender = classify_gender(voice)
        voices[gender].setdefault(voice, []).append(person)
    return voices


def list_output_devices():
    try:
        return [d['name'] for d in sd.query_devices() if d['max_output_channels'] > 0]
    except Exception:
        return []


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


class GeneralTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=16)
        self.settings = load_settings()

        self.region_var = tk.StringVar(value=self.settings.get("region", "US"))
        self.speed_var = tk.DoubleVar(value=self.settings.get("speed", 1.0))
        self.male_volume_var = tk.DoubleVar(value=self.settings.get("malevolume", 0.7))
        self.female_volume_var = tk.DoubleVar(value=self.settings.get("femalevolume", 0.7))
        self.audio_device_var = tk.StringVar(value=self.settings.get("audio_device", "auto"))

        ttk.Label(self, text="Region").grid(row=0, column=0, sticky="w", pady=6)
        region_frame = ttk.Frame(self)
        region_frame.grid(row=0, column=1, sticky="w")
        ttk.Radiobutton(region_frame, text="US (American English)", variable=self.region_var, value="US").pack(anchor="w")
        ttk.Radiobutton(region_frame, text="UK (British English)", variable=self.region_var, value="UK").pack(anchor="w")

        ttk.Label(self, text="TTS speed").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Spinbox(self, from_=0.5, to=2.0, increment=0.1, textvariable=self.speed_var, width=8).grid(row=1, column=1, sticky="w")

        ttk.Label(self, text="Male voice volume").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Spinbox(self, from_=0.0, to=2.0, increment=0.1, textvariable=self.male_volume_var, width=8).grid(row=2, column=1, sticky="w")

        ttk.Label(self, text="Female voice volume").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Spinbox(self, from_=0.0, to=2.0, increment=0.1, textvariable=self.female_volume_var, width=8).grid(row=3, column=1, sticky="w")

        ttk.Label(self, text="Audio output device").grid(row=4, column=0, sticky="w", pady=6)
        ttk.Combobox(
            self, textvariable=self.audio_device_var,
            values=["auto"] + list_output_devices(), width=32,
        ).grid(row=4, column=1, sticky="w")
        ttk.Label(
            self, text="\"auto\" picks pipewire/pulse over the generic ALSA default.\nOnly change this if audio doesn't play or comes out the wrong device.",
            justify="left", foreground="gray",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 6))

        ttk.Button(self, text="Save", command=self.save).grid(row=6, column=0, columnspan=2, pady=16)

    def save(self):
        try:
            self.settings["region"] = self.region_var.get()
            self.settings["speed"] = float(self.speed_var.get())
            self.settings["malevolume"] = float(self.male_volume_var.get())
            self.settings["femalevolume"] = float(self.female_volume_var.get())
            self.settings["audio_device"] = self.audio_device_var.get().strip() or "auto"
            save_settings(self.settings)
        except (tk.TclError, ValueError) as exc:
            messagebox.showerror("Invalid value", str(exc))
            return
        messagebox.showinfo("Saved", "Settings saved.")


class NamePronunciationTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=16)

        ttk.Label(
            self,
            text=(
                "Ask ChatGPT how to pronounce your in-game name using the IPA alphabet,\n"
                "e.g. \"Minhaka\" -> \"miˈɲaka\". You can test pronunciations at ipa-reader.com."
            ),
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        ttk.Label(self, text="In-game name").grid(row=1, column=0, sticky="w", pady=6)
        self.name_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.name_var, width=30).grid(row=1, column=1, sticky="w")

        ttk.Label(self, text="IPA pronunciation").grid(row=2, column=0, sticky="w", pady=6)
        self.pronunciation_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.pronunciation_var, width=30).grid(row=2, column=1, sticky="w")

        ttk.Button(self, text="Save", command=self.save).grid(row=3, column=0, columnspan=2, pady=16)

    def save(self):
        name = self.name_var.get().strip()
        pronunciation = self.pronunciation_var.get().strip()
        if not name or not pronunciation:
            messagebox.showerror("Missing value", "Both name and pronunciation are required.")
            return
        content = build_name_lexicon(name, pronunciation)
        os.makedirs(os.path.dirname(NAME_LEXICON_PATH), exist_ok=True)
        with open(NAME_LEXICON_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        messagebox.showinfo("Saved", "Name pronunciation saved.")


class VoiceRow:
    def __init__(self, parent, on_remove, person="", voice=""):
        self.frame = ttk.Frame(parent)
        self.person_var = tk.StringVar(value=person)
        self.voice_var = tk.StringVar(value=voice)

        ttk.Entry(self.frame, textvariable=self.person_var, width=28).pack(side="left", padx=(0, 8))
        ttk.Combobox(self.frame, textvariable=self.voice_var, values=ALL_VOICES, width=14).pack(side="left", padx=(0, 8))
        ttk.Button(self.frame, text="Remove", command=lambda: on_remove(self)).pack(side="left")

        self.frame.pack(fill="x", pady=2)

    def values(self):
        return self.person_var.get(), self.voice_var.get()

    def destroy(self):
        self.frame.destroy()


class VoiceAssignmentsTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=16)
        self.rows = []

        canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.rows_frame = ttk.Frame(canvas)
        self.rows_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="top", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        button_bar = ttk.Frame(self)
        button_bar.pack(side="bottom", fill="x", pady=12)
        ttk.Button(button_bar, text="Add character", command=self.add_row).pack(side="left")
        ttk.Button(button_bar, text="Save", command=self.save).pack(side="left", padx=8)

        for person, voice in flatten_voices(load_voices()):
            self.add_row(person, voice)

    def add_row(self, person="", voice=""):
        row = VoiceRow(self.rows_frame, self.remove_row, person, voice)
        self.rows.append(row)

    def remove_row(self, row):
        self.rows.remove(row)
        row.destroy()

    def save(self):
        rows = [row.values() for row in self.rows]
        save_voices(regroup_voices(rows))
        messagebox.showinfo("Saved", "Voice assignments saved.")


class SettingsApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FFXIV TTS Settings")
        self.geometry("520x480")

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)
        notebook.add(GeneralTab(notebook), text="General")
        notebook.add(NamePronunciationTab(notebook), text="Name Pronunciation")
        notebook.add(VoiceAssignmentsTab(notebook), text="Voice Assignments")


if __name__ == "__main__":
    SettingsApp().mainloop()

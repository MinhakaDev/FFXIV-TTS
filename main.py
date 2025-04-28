import os
import json
import websocket
from kokoro import KPipeline
import sounddevice as sd
import xml.etree.ElementTree as ET
import time

with open('./settings.json', 'r', encoding='utf-8') as f:
    settings = json.load(f)
if settings['region'] == "US":
    femaleVoice = 'af_heart'
    maleVoice = 'am_adam'
else:
    femaleVoice = 'bf_emma'
    maleVoice = 'bm_george'
voiceSpeed = settings['speed']
femaleVoiceVolume = settings['femalevolume']
maleVoiceVolume = settings['malevolume']



def parse_pls(filename):
    lexicon = {}
    try:
        tree = ET.parse(filename)
        root = tree.getroot()
        
        # Iterate over each lexeme in the PLS file
        for lexeme in root.findall('{http://www.w3.org/2005/01/pronunciation-lexicon}lexeme'):
            graphemes = [
                grapheme.text.strip()
                for grapheme in lexeme.findall('{http://www.w3.org/2005/01/pronunciation-lexicon}grapheme')
            ]
            phoneme = lexeme.find('{http://www.w3.org/2005/01/pronunciation-lexicon}phoneme').text.strip()
            
            # Add entries to the lexicon
            for grapheme in graphemes:
                lexicon[grapheme] = phoneme
    except Exception as e:
        print(f"Error parsing PLS file {filename}: {e}")
    return lexicon


# Directories containing PLS files
pls_directories = [
    './lexicons/Characters-Locations-System',
    './lexicons/Chat-FFXIV-Acronyms',
    './lexicons/Stutter-Replacers'
    './lexicons/Your-Name'
]


# Configure Kokoro pipeline
pipeline = KPipeline(lang_code='b')

for directory in pls_directories:
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith('.pls'):
                pls_file_path = os.path.join(root, file)
                print(f"Processing PLS file: {pls_file_path}")
                custom_lexicon = parse_pls(pls_file_path)
                pipeline.g2p.lexicon.golds.update(custom_lexicon)


def on_open(ws):
    print("Connected to WebSocket server")

def on_message(ws, message):
    try:
        data = json.loads(message)

        # stop audio if playing
        if data.get('Type') == "Cancel":
            print("skipping audio")
            sd.stop()

        if data.get('Type') == 'Say':
            payload = data.get('Payload', '')
            voice_type = data.get('Voice', {}).get('Name', '').lower()

            print("Say Payload:", payload)
            print("Voice:", voice_type or "Unknown")

            # Pick voice
            voice = maleVoice
            if voice_type == 'female':
                voice = femaleVoice

            # Generate audio using Kokoro
            generator = pipeline(payload, voice=voice, speed=voiceSpeed)

            # Stream the audio using sounddevice
            for i, (gs, ps, audio) in enumerate(generator):
                print(f"Streaming audio segment {i + 1}...")
                adjusted_audio = audio * maleVoiceVolume
                if voice == femaleVoice:
                    adjusted_audio = audio * femaleVoiceVolume
                sd.play(adjusted_audio, samplerate=24000)

    except Exception as e:
        print(f"Error parsing or processing message: {e}")

def on_error(ws, error):
    error_str = str(error)
    if "10061" in error_str:
        print("You didn't open FFXiv")
    elif "10053" in error_str:
        print("Error in Processing Request Reconecting ...")
        connect()
    else:
        print(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    print("WebSocket connection closed")


def connect():
    websocket_url = "ws://localhost:51363/Messages"
    ws = websocket.WebSocketApp(
        websocket_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    ws.run_forever()

if __name__ == "__main__":
    connect()

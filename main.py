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
    reagionVoice = "a"
    femaleVoice = 'af_heart'
    maleVoice = 'am_puck'
else:
    reagionVoice = "b"
    femaleVoice = 'bf_emma'
    maleVoice = 'bm_fable'
voiceSpeed = settings['speed']
femaleVoiceVolume = settings['femalevolume']
maleVoiceVolume = settings['malevolume']
print(f"\n\nUsing Male Voice: {maleVoice} and Female voice: {femaleVoice}\n\n")

aliases_dict = {}

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
                if grapheme.text is not None
            ]
            
            phoneme_element = lexeme.find('{http://www.w3.org/2005/01/pronunciation-lexicon}phoneme')
            phoneme = phoneme_element.text.strip() if phoneme_element is not None and phoneme_element.text else None
            
            alias_element = lexeme.find('{http://www.w3.org/2005/01/pronunciation-lexicon}alias')
            alias = alias_element.text.strip() if alias_element is not None and alias_element.text else None
            
            # Add entries to the lexicon and aliases_dict
            for grapheme in graphemes:
                if phoneme:  # Only add to lexicon if phoneme exists
                    lexicon[grapheme] = phoneme
                if alias:  # Only add to aliases_dict if alias exists
                    aliases_dict[grapheme] = alias
    except Exception as e:
        print(f"Error parsing PLS file {filename}: {e}")
    
    return lexicon


def transform_string(input_string, aliases_dict):
    # Split the string into words
    words = input_string.split()
    
    # Transform words that match a grapheme in aliases_dict
    transformed_words = [
        aliases_dict[word] if word in aliases_dict else word
        for word in words
    ]
    
    # Join the words back into a string
    transformed_string = ' '.join(transformed_words)
    
    return transformed_string

def getVoice(person,currentVoice):
    if person == "alphinaud":
        return "bm_fable"
    
    elif person == "alisaie":
        return "bf_emma"
    
    elif person == "wuk lamat":
        return "bm_daniel"
    
    elif person == "y'shtola":
        return "bf_alice"
    
    elif person == "g'raha tia":
        return "bf_isabella"
    
    elif person == "thancred":
        return "bm_fable"
    
    elif person == "krile":
        return "bf_lily"
    
    elif person == "urianger":
        return "bm_lewis"
    
    elif person == "lyse":
        return "af_heart"
    
    elif person == "erenville":
        return "am_fenrir"
    
    elif person == "estinien":
        return "af_bella"
    
    elif person == "minfilia":
        return "f_heart"
    


    elif person == "zero":
        return "am_puck"
    
    elif person == "emet-selch":
        return ""
    
    elif person == "raubahn":
        return "am_fenrir"
    
    elif person == "hien":
        return "am_michael"
    
    elif person == "tataru":
        return "am_michael"
    
    elif person == "cid":
        return "bm_fable"
    
    elif person == "elidibus":
        return "am_michael"
    elif person == "yugiri":
        return "af_bella"
    elif person == "gosetsu":
        return "zm_yunjian"
    
    return currentVoice


# Directories containing PLS files
pls_directories = [
    './lexicons/Characters-Locations-System',
    './lexicons/Your-Name',
    './lexicons/Stutter-Replacers',
    './lexicons/Chat-FFXIV-Acronyms'
]


# Configure Kokoro pipeline
pipeline = KPipeline(lang_code=reagionVoice)

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
            text = data.get('Payload', '')
            print(f"\n{str(data)}\n")
            personSpeaking = data.get('Speaker','').lower()

            print(f"\n{personSpeaking}\n")
            voice_type = data.get('Voice', {}).get('Name', '').lower()


            payload = transform_string(text, aliases_dict)



            print("Say Payload:", payload)
            print("Voice:", voice_type or "Unknown")
            print(f"Person: {personSpeaking}")

            # Pick voice
            voice = maleVoice
            if voice_type == 'female':
                voice = femaleVoice
            voice = getVoice(personSpeaking,voice)
            # Generate audio using Kokoro
            generator = pipeline(payload, voice=voice, speed=voiceSpeed)
            print(f"with voice {voice}")
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
        print("You didn't open FFXIV Launcher")
        for i in range(5):
            print(f"sleaping in {5-i}")
            time.sleep(1)
            i+=1
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

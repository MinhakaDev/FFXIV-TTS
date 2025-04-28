import os
import json




def __main__():
    print("""   Please Select What You Want to Configure Choosing The Number (0,1,2,3,4)\n0. EXIT\n1. Pronunciation of Your Ingame Name\n2. Change to British English or American English\n3. TTS Reading Speed\n4. The Volume of the TTS""")
    choice = int(input())
    if choice == 1:
        ChangeName()
    elif choice == 2:
         ChangeRegion()
    elif choice == 3:
         ChangeSpeed()
    elif choice == 4:
         ChangeSpeed()
    elif choice == 0:
         return False
    return True


def ChangeName():
        name = input("Enter your in-game name: ").strip()
        print("\nNow go to ChatGPT and ask how to pronounce your name using IPA lexicon.")
        print("Example: If 'mi' is like 'mee', 'nha' like 'nya', and 'ka' like 'ka', ChatGPT would respond like (miˈɲaka).")
        print("You can test your pronunciation at: https://ipa-reader.com\n")
        pronunciation = input("Enter your name pronunciation in IPA: ").strip()
        if not pronunciation:
            print("Error: Pronunciation cannot be empty.")
            return

        # Prepare both versions
        name_upper = name[0].upper() + name[1:]
        name_lower = name[0].lower() + name[1:]

        content = f"""<?xml version='1.0' encoding='UTF-8'?>
<lexicon xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" version="1.0"
    xsi:schemaLocation="http://www.w3.org/2005/01/pronunciation-lexicon http://www.w3.org/TR/2007/CR-pronunciation-lexicon-20071212/pls.xsd"
    alphabet="ipa" xml:lang="en" xmlns="http://www.w3.org/2005/01/pronunciation-lexicon">
    <lexeme>
        <grapheme>{name_lower}</grapheme>
        <grapheme>{name_upper}</grapheme>
        <phoneme>{pronunciation}</phoneme>
    </lexeme>
</lexicon>"""
        # Write the updated XML back
        with open('./lexicons/Your-Name/lexicon.pls', 'w', encoding='utf-8') as file:
            file.write(content)
        with open('./lexicons/Your-Name/lexicon.pls', 'r', encoding='utf-8') as file:
            newcontent = file.read()
        if content == newcontent:
             print("\n✅ Name updated successfully!")
        else:
             print("❌ Error while trying to write your name \n\n")


def ChangeRegion():
        print("BE AWARE THAT IN AMERICAN ENGLISH SOME OF THE GAME NAMES PRONUNCIATION IS WAY OF. \n\n")
        with open('./settings.json', 'r', encoding='utf-8') as f:
            settings = json.load(f)

        print("Select your region:\n1. British English (UK)\n2. American English (US)")
        region_choice = int(input())

        if region_choice == 1:
            settings['region'] = 'UK'
        elif region_choice == 2:
            settings['region'] = 'US'
        else:
            print("Invalid choice. No changes made.")
            return

        with open('settings.json', 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=4)

        print(f"Region set to {settings['region']} successfully!")

def ChangeSpeed():

        with open('./settings.json', 'r', encoding='utf-8') as f:
            settings = json.load(f)

        print("Select your speed like (1.2 , 1.0,  0.7).")
        speed = float(input())

        settings['speed'] = speed

        with open('settings.json', 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=4)

        print(f"Speed set to {settings['speed']} successfully!")

def ChangeSpeed():

        with open('./settings.json', 'r', encoding='utf-8') as f:
            settings = json.load(f)

        print("Select your speed ")
        volume = int(input())

        settings['volume'] = volume

        with open('settings.json', 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=4)

        print(f"Volume set to {settings['volume']} successfully!")


isRunning = True
while isRunning:
     isRunning =__main__()
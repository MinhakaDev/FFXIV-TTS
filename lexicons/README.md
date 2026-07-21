# Lexicons

Pronunciation fixes, in the W3C PLS format. Sourced from
[karashiiro/TextToTalk](https://github.com/karashiiro/TextToTalk/tree/main/lexicons)
(authors: johnysandels & ryankhart), except `Your-Name`, which the app writes itself.

The app does **not** scan this folder. It reads only the packages listed in
`LEXICON_DIRECTORIES` in `src/tts.py`, in that order, merging each one over the last —
so a later package wins any grapheme both of them define.

## Active

| Package | What it does |
| --- | --- |
| `Characters-Locations-System` | The main one: ~205 character and place names. |
| `Your-Name` | Your character's name. Rewritten by the Name screen in the app. |
| `Stutter-Replacers` | Makes written stutters ("W-what") sound less robotic. |
| `Chat-FFXIV-Acronyms` | "NIN" as "ninja" rather than spelled out. |
| `Unconfirmed-Name-Pronunciations` | Speculative names not yet heard in voice acting. |
| `Americanize-pronunciations` | Forces US "lieutenant"/"clerk" even on UK voices. |

## Present but inactive

These conflict with `Characters-Locations-System` on purpose. They are kept here so you
can opt in — add the folder name to `LEXICON_DIRECTORIES` **after**
`Characters-Locations-System` so it takes precedence.

| Package | Why it's off |
| --- | --- |
| `Characters-Locations-Polly` | The Amazon Polly variant of the main lexicon: 206 of its entries are identical, 17 disagree, and only 10 are new. Its tuning targets Polly's voices, not Kokoro. |
| `Yugiri-Western-Pronunciation` | Exists solely to override one entry — "Yugiri" with a hard western `r`. Purely a taste call. |

## A caveat on phonemes

These lexicons were written for Windows SAPI and Amazon Polly. Kokoro runs its own G2P
(misaki), which does not always read the same IPA string the same way, so an imported
entry can sound worse than the default.

The Polly file is the clearest example, and the main reason it stays inactive: several of
its entries use notation misaki has no reason to understand — `tʊliˈjoʊ ‖ ˈlʌl`
(Tuliyollal) with an IPA major-break bar, `fo(r)ˈdəʊlə` (Fordola) with a parenthesised
optional r, `ʑb. ˈr. ˈɔl` (Xbr'aal), and `əˌmald͡ʒæ z` (Amalj'aas) with a stray space
that reads as a word boundary.

Every file here is currently byte-identical to upstream, so re-syncing is a plain copy.
If you hand-tune an entry, note it here.

# Lexicons

Pronunciation fixes, in the W3C PLS format. Sourced from
[karashiiro/TextToTalk](https://github.com/karashiiro/TextToTalk/tree/main/lexicons)
(authors: johnysandels & ryankhart), except `Your-Name`, which the app writes itself, and
`Qwen3-Respellings`, which is ours.

The app does **not** scan this folder. It reads only the packages listed in
`LEXICON_DIRECTORIES` in `src/tts.py`, in that order, merging each one over the last —
so a later package wins any grapheme both of them define.

## Active

| Package | What it does |
| --- | --- |
| `Characters-Locations-System` | The main one: ~205 character and place names. |
| `Qwen3-Respellings` | Qwen3 only: 63 of those names spelled the way they sound (see below). |
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

Every file here but `Your-Name` and `Qwen3-Respellings` is currently byte-identical to
upstream, so re-syncing is a plain copy. If you hand-tune an entry, note it here.

## Qwen3-Respellings

Qwen3-TTS reads plain text, so it ignores every `<phoneme>` entry above and only gets the
`<alias>` ones. This package gives it respellings (`Alphinaud` → `Al-fee-noh`) of
`Characters-Locations-System`'s names, taken from their IPA. `QWEN_ONLY_LEXICONS` in
`src/tts.py` keeps Kokoro from loading it, since Kokoro has the IPA.

It holds only the 63 names out of ~150 that Qwen3 said wrong as written and right
respelled, judged by what Whisper (`whisper-base.en`) heard in each name read both ways.
Respelling a name Qwen3 already said right made it choppy: *Tataru* came out as "Ta! Ta!
Ru". Two other things learned that way:

- Use hyphens between syllables, all lowercase after the first letter. Qwen3 spells a
  capitalized syllable out as letters: `oo-ree-AHN-zhay` came out as "U-R-N-J".
- Avoid a syllable that reads as a word of its own, like a final `-uh` (`Kuh-loo-see-uh`
  became "Kulu C. Ugh").

An alias matches one word, so a two-word name gets an entry per word (`Mheg` for Il Mheg),
and `'s` on the end is handled for you: `Alphinaud's` finds `Alphinaud`.

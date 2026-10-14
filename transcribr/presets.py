"""Presets by kind of recording.

Once each 30-second window is transcribed on its own, there is little
to gain from presets that fiddle with decoder internals. What differs
between an ERISP and a dictation is: which engine reads that kind of
audio best, how long a pause means a new paragraph, how many speakers
to expect, and whether a change of voice should break a paragraph.

Each preset therefore sets four things and says why. `model` is used
for every language; `model_en` replaces it in English, where Parakeet
is available. Everything else - the output format, speaker detection,
the decoder settings - is left exactly as the user has it.
"""

from .engines import PARAKEET_MODEL

TURBO = "large-v3-turbo"

PRESETS = [
    {
        "id": "general",
        "label": "General",
        "note": "The defaults: Whisper's fastest accurate model, a new "
                "paragraph after a second of silence.",
        "model": TURBO, "model_en": TURBO,
        "gap": 1.0, "num_speakers": 2, "voice_breaks": True,
    },
    {
        "id": "interview",
        "label": "Police interview (ERISP, DVEC)",
        "note": "In English, NVIDIA's Parakeet, which is quick and does "
                "not fall into repetition loops. Two speakers, and a "
                "new paragraph whenever the voice changes.",
        "model": TURBO, "model_en": PARAKEET_MODEL,
        "gap": 1.0, "num_speakers": 2, "voice_breaks": True,
    },
    {
        "id": "phone",
        "label": "Phone call",
        "note": "Two speakers, as for an interview. Call recorders often "
                "put each speaker on their own channel, which Transcribr "
                "uses automatically when it finds it.",
        "model": TURBO, "model_en": PARAKEET_MODEL,
        "gap": 1.0, "num_speakers": 2, "voice_breaks": True,
    },
    {
        "id": "hearing",
        "label": "Hearing or court",
        "note": "Whisper, which copes well with courtroom audio. "
                "Several speakers, so the number is left for "
                "you to set.",
        "model": TURBO, "model_en": TURBO,
        "gap": 1.0, "num_speakers": 0, "voice_breaks": True,
    },
    {
        "id": "meeting",
        "label": "Meeting or conference",
        "note": "Whisper, several speakers. Meetings are hard "
                "recordings to transcribe: expect to lose words wherever "
                "people talk over each other.",
        "model": TURBO, "model_en": TURBO,
        "gap": 1.0, "num_speakers": 0, "voice_breaks": True,
    },
    {
        "id": "bwv",
        "label": "Body-worn video",
        "note": "Whisper, several speakers. Body-worn recordings vary "
                "widely — treat this as a starting point and adjust "
                "to suit.",
        "model": TURBO, "model_en": TURBO,
        "gap": 1.0, "num_speakers": 0, "voice_breaks": True,
    },
    {
        "id": "dictation",
        "label": "Dictation (one speaker)",
        "note": "One speaker, a longer pause before a new paragraph, and "
                "no breaking on a change of voice — there is only one.",
        "model": TURBO, "model_en": TURBO,
        "gap": 1.5, "num_speakers": 1, "voice_breaks": False,
    },
]

# The settings a preset touches. Anything else is the user's own.
PRESET_KEYS = ("model", "gap", "num_speakers", "voice_breaks")


def preset(preset_id):
    return next((p for p in PRESETS if p["id"] == preset_id), None)


def settings_for(preset_id, english=True):
    """The settings this preset applies, or {} for an unknown one."""
    p = preset(preset_id)
    if not p:
        return {}
    return {"model": p["model_en"] if english else p["model"],
            "gap": p["gap"], "num_speakers": p["num_speakers"],
            "voice_breaks": p["voice_breaks"]}


def matching_preset(settings, english=True):
    """Which preset these settings are, or None when they are not one -
    so the interface can say "Custom" rather than name a preset that is
    not what is actually set."""
    for p in PRESETS:
        want = settings_for(p["id"], english)
        if all(settings.get(k) == v for k, v in want.items()):
            return p["id"]
    return None

"""Presets by kind of recording (transcribr/presets.py)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _harness  # noqa: F401

import transcribr as T
from transcribr.engines import ALL_MODELS, PARAKEET_MODEL
from transcribr.presets import PRESETS, PRESET_KEYS, matching_preset, settings_for


class TestThePresets(unittest.TestCase):

    def test_every_preset_names_models_that_exist(self):
        for p in PRESETS:
            self.assertIn(p["model"], ALL_MODELS, p["id"])
            self.assertIn(p["model_en"], ALL_MODELS, p["id"])

    def test_every_preset_explains_itself(self):
        for p in PRESETS:
            self.assertTrue(p["label"] and len(p["note"]) > 30, p["id"])

    def test_settings_are_valid_settings(self):
        """A preset may only set keys the settings contract knows, and
        the values must survive validation unchanged."""
        base = T.default_settings()
        for p in PRESETS:
            want = settings_for(p["id"])
            self.assertEqual(set(want), set(PRESET_KEYS))
            got = T.validate_settings(want, base)
            for k, v in want.items():
                self.assertEqual(got[k], v, f"{p['id']}.{k}")

    def test_interview_uses_parakeet_in_english_only(self):
        self.assertEqual(settings_for("interview", True)["model"],
                         PARAKEET_MODEL)
        self.assertEqual(settings_for("interview", False)["model"],
                         "large-v3-turbo")

    def test_court_keeps_whisper(self):
        """Parakeet was clearly worse on court audio."""
        self.assertNotEqual(settings_for("hearing", True)["model"],
                            PARAKEET_MODEL)

    def test_dictation_expects_one_voice(self):
        d = settings_for("dictation")
        self.assertEqual(d["num_speakers"], 1)
        self.assertFalse(d["voice_breaks"])
        self.assertGreater(d["gap"], settings_for("interview")["gap"])

    def test_an_unknown_preset_changes_nothing(self):
        self.assertEqual(settings_for("nonsense"), {})


class TestMatching(unittest.TestCase):

    def test_the_defaults_are_the_general_preset(self):
        self.assertEqual(matching_preset(T.default_settings()), "general")

    def test_applying_one_makes_it_the_active_one(self):
        s = dict(T.default_settings(), **settings_for("dictation"))
        self.assertEqual(matching_preset(s), "dictation")

    def test_changing_one_value_means_custom(self):
        s = dict(T.default_settings(), **settings_for("interview"))
        s["gap"] = 2.2
        self.assertIsNone(matching_preset(s))

    def test_language_is_taken_into_account(self):
        s = dict(T.default_settings(), **settings_for("interview", True))
        self.assertEqual(matching_preset(s, True), "interview")
        self.assertIsNone(matching_preset(s, False))


if __name__ == "__main__":
    unittest.main()

"""The Python/TypeScript contract.

The front-end used to declare every JSON shape by hand, so a renamed
settings key was a silent `undefined` in the browser. The shapes Python
owns are generated now; these tests are what stops the generated file
and the hand-written half drifting away from Python underneath them.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import transcribr as T
    from transcribr.contract import typescript_contract
except ImportError as e:
    raise unittest.SkipTest(f"cannot import transcribr: {e}")

GENERATED = ROOT / "web" / "src" / "api" / "contract.generated.ts"
TYPES = ROOT / "web" / "src" / "api" / "types.ts"
REGENERATE = ("Regenerate it with:\n"
              "    python3 -m transcribr.contract "
              "web/src/api/contract.generated.ts")


class TestGeneratedContract(unittest.TestCase):
    def test_the_checked_in_file_is_current(self):
        """The whole point: this fails the moment Python changes and the
        front-end has not been regenerated."""
        self.assertTrue(GENERATED.exists(),
                        f"{GENERATED} is missing.\n{REGENERATE}")
        on_disk = GENERATED.read_text(encoding="utf-8")
        self.assertEqual(
            on_disk, typescript_contract(),
            f"\n\n{GENERATED.relative_to(ROOT)} is out of date.\n"
            f"{REGENERATE}")

    def test_every_setting_appears_in_the_interface(self):
        body = self._interface("Settings", typescript_contract())
        declared = set(re.findall(r"^  (\w+):", body, re.M))
        self.assertEqual(declared, set(T.default_settings()))

    def test_booleans_and_numbers_are_typed_as_such(self):
        body = self._interface("Settings", typescript_contract())
        types = dict(re.findall(r"^  (\w+): ([^;]+);", body, re.M))
        for key in T._SETTINGS_BOOL_KEYS:
            self.assertEqual(types[key], "boolean", key)
        for key in T._SETTINGS_NUMBER_KEYS:
            self.assertEqual(types[key], "number", key)

    def test_fixed_choices_become_unions_not_bare_strings(self):
        from transcribr.contract import FIXED_CHOICE_SETTINGS
        body = self._interface("Settings", typescript_contract())
        types = dict(re.findall(r"^  (\w+): ([^;]+);", body, re.M))
        choices = T._settings_choices()
        for key in FIXED_CHOICE_SETTINGS:
            for value in choices[key]:
                self.assertIn(f'"{value}"', types[key])

    def test_dynamic_choices_stay_strings(self):
        """engine/model/language depend on the machine, so pinning them
        to this machine's list would be wrong on the next one."""
        body = self._interface("Settings", typescript_contract())
        types = dict(re.findall(r"^  (\w+): ([^;]+);", body, re.M))
        for key in ("engine", "model", "language", "diarize_model"):
            self.assertEqual(types[key], "string", key)

    def test_palette_keys_match_python(self):
        body = self._interface("Palette", typescript_contract())
        declared = set(re.findall(r"^  (\w+):", body, re.M))
        self.assertEqual(declared, set(T._PALETTES["light"]))

    def test_speaker_slots_match_the_palette(self):
        ts = typescript_contract()
        m = re.search(r"speaker_colours: Record<([^,]+), string>", ts)
        slots = set(re.findall(r'"(\w+)"', m.group(1)))
        self.assertEqual(slots,
                         set(T._PALETTES["light"]["speaker_colours"]))

    def test_run_phases_match_the_controller(self):
        body = re.search(r"export type RunPhase =\n(.*?);", typescript_contract(),
                         re.S).group(1)
        self.assertEqual(re.findall(r'"(\w+)"', body), list(T.RUN_PHASES))

    @staticmethod
    def _interface(name, source):
        m = re.search(r"export interface %s \{\n(.*?)\n\}" % name,
                      source, re.S)
        assert m, f"no interface {name}"
        return m.group(1)


class TestHandWrittenHalf(unittest.TestCase):
    """types.ts still declares the shapes that route handlers assemble.
    These keep the seam between the two halves honest."""

    def test_types_ts_does_not_redeclare_a_generated_shape(self):
        source = TYPES.read_text(encoding="utf-8")
        for name in ("Settings", "Palette"):
            self.assertNotIn(f"export interface {name} {{", source,
                             f"{name} is generated; types.ts must "
                             f"re-export it rather than declare it again.")
        self.assertNotIn("export type RunPhase =", source)

    def test_types_ts_re_exports_the_generated_names(self):
        source = TYPES.read_text(encoding="utf-8")
        self.assertIn('from "./contract.generated"', source)
        for name in ("Settings", "Palette", "RunPhase", "ThemeSetting"):
            self.assertIn(name, source)

    def test_the_settings_route_serves_exactly_the_declared_keys(self):
        """/api/settings answers with validate_settings' output, so the
        interface has to describe that and nothing else."""
        served = set(T.validate_settings({}))
        self.assertEqual(served, set(T.default_settings()))

"""Speakers on separate channels.

Every recording shape that must be read as one-speaker-per-channel, and
every one that must not: mono copied to two channels and a stereo pair
in a room both arrive as stereo, and labelling them by channel would
put words in the wrong mouths. The signals are synthetic speech-like
bursts, so every case knows exactly who spoke when.
"""

import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _harness  # noqa: F401

try:
    import numpy as np
    import av  # noqa: F401
except ImportError as e:
    raise unittest.SkipTest(f"needs numpy and av: {e}")

try:
    import transcribr as T
    from transcribr import channels as C
    from transcribr import diarize as D
except ImportError as e:
    raise unittest.SkipTest(f"cannot import transcribr: {e}")

SR = 16000


class _Signals:
    """Two speakers taking turns, with pauses; `truth` is who spoke when."""

    def __init__(self, seconds=45, seed=1):
        self.rng = np.random.default_rng(seed)
        n = seconds * SR
        self.a, self.b = np.zeros(n), np.zeros(n)
        self.truth, t, who = [], 0.5, 0
        while t < seconds - 5:
            d = self.rng.uniform(1.5, 4.0)
            s = self.burst(d)
            i = int(t * SR)
            (self.a if who == 0 else self.b)[i:i + len(s)] += s
            self.truth.append((t, t + d, who))
            t += d + self.rng.uniform(0.3, 1.2)
            who ^= 1

    def burst(self, seconds):
        """Band-limited noise with a syllable-rate envelope: close
        enough to speech for a loudness measurement."""
        n = int(seconds * SR)
        x = self.rng.standard_normal(n)
        spec = np.fft.rfft(x)
        f = np.fft.rfftfreq(n, 1 / SR)
        spec[(f < 150) | (f > 3500)] = 0
        x = np.fft.irfft(spec, n)
        env = 0.55 + 0.45 * np.sin(2 * np.pi * 4 * np.arange(n) / SR)
        return x / np.sqrt(np.mean(x ** 2)) * env * 10 ** (-25 / 20)

    def noise(self):
        return self.rng.standard_normal(len(self.a)) * 10 ** (-65 / 20)


def _write_wav(path, channels):
    x = np.clip(np.stack(channels, 1), -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    return path


def _write_tracks(path, tracks):
    """One mono audio track per signal in a single container - how
    conferencing exports and some court systems deliver them."""
    with av.open(str(path), "w", format="matroska") as out:
        streams = []
        for _ in tracks:
            st = out.add_stream("pcm_s16le", rate=SR)
            st.layout = "mono"
            streams.append(st)
        hop = SR // 10
        for i in range(0, len(tracks[0]), hop):
            for st, x in zip(streams, tracks):
                block = (np.clip(x[i:i + hop], -1, 1) * 32767) \
                    .astype("<i2").reshape(1, -1)
                frame = av.AudioFrame.from_ndarray(block, format="s16",
                                                   layout="mono")
                frame.sample_rate = SR
                frame.pts = i
                for pkt in st.encode(frame):
                    out.mux(pkt)
        for st in streams:
            for pkt in st.encode(None):
                out.mux(pkt)
    return path


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sig = _Signals()

    def attribution(self, turns):
        """(share of true speech credited to the right channel, share
        credited to a wrong one)."""
        right = wrong = total = 0.0
        for s, e, who in self.sig.truth:
            total += e - s
            for ts, te, c in turns:
                o = max(0.0, min(e, te) - max(s, ts))
                if c == who:
                    right += o
                else:
                    wrong += o
        return right / total, wrong / total


class TestMustSeparate(_Case):

    def check(self, path):
        found = C.separated_channel_turns(path)
        self.assertIsNotNone(found, "channels should read as separate")
        turns, verdict = found
        self.assertEqual(verdict["used"], [0, 1])
        right, wrong = self.attribution(turns)
        self.assertGreater(right, 0.95)
        self.assertLess(wrong, 0.02)

    def test_one_microphone_each_with_bleed(self):
        s = self.sig
        self.check(_write_wav(self.tmp / "x.wav", [
            s.a + 0.18 * s.b + s.noise(), s.b + 0.18 * s.a + s.noise()]))

    def test_very_different_channel_gains(self):
        """Court systems run channels at different gains. Judged against
        its own floor, a channel 18 dB hotter must not win every frame."""
        s = self.sig
        self.check(_write_wav(self.tmp / "x.wav", [
            s.a + 0.18 * s.b + s.noise(),
            (s.b + 0.18 * s.a + s.noise()) * 8]))

    def test_separate_tracks_in_one_file(self):
        s = self.sig
        self.check(_write_tracks(self.tmp / "x.mkv", [
            s.a + 0.18 * s.b + s.noise(), s.b + 0.18 * s.a + s.noise()]))

    def test_talking_over_each_other_credits_both(self):
        s = self.sig
        i, j = 10 * SR, 12 * SR
        a, b = s.a.copy(), s.b.copy()
        a[i:j] += s.burst(2)
        b[i:j] += s.burst(2)
        turns, _ = C.separated_channel_turns(_write_wav(
            self.tmp / "x.wav", [a + 0.18 * b + s.noise(),
                                 b + 0.18 * a + s.noise()]))
        for ch in (0, 1):
            self.assertTrue(any(ts <= 11 <= te for ts, te, c in turns
                                if c == ch), f"channel {ch} missing at 11s")


class TestMustNotSeparate(_Case):

    def test_mono_file_is_dismissed_from_the_header(self):
        s = self.sig
        path = _write_wav(self.tmp / "x.wav", [s.a + s.b + s.noise()])
        with mock.patch.object(C, "_levels_one_stream") as read:
            self.assertIsNone(C.separated_channel_turns(path))
        read.assert_not_called()

    def test_mono_copied_to_both_channels(self):
        s = self.sig
        mix = s.a + s.b + s.noise()
        self.assertIsNone(C.separated_channel_turns(
            _write_wav(self.tmp / "x.wav", [mix, mix])))

    def test_stereo_pair_in_a_room(self):
        """Both microphones hear both people, a couple of dB apart."""
        s = self.sig
        self.assertIsNone(C.separated_channel_turns(_write_wav(
            self.tmp / "x.wav", [s.a + 0.8 * s.b + s.noise(),
                                 0.8 * s.a + s.b + s.noise()])))

    def test_one_channel_carries_everything(self):
        s = self.sig
        self.assertIsNone(C.separated_channel_turns(_write_wav(
            self.tmp / "x.wav", [s.a + s.b + s.noise(), s.noise()])))

    def test_unreadable_file(self):
        bad = self.tmp / "x.wav"
        bad.write_bytes(b"not audio at all")
        self.assertIsNone(C.separated_channel_turns(bad))


class TestSpeakerTurns(_Case):
    """The one entry point both run paths use."""

    def run_it(self, path, audio=None):
        import queue
        q = queue.Queue()
        turns = D.speaker_turns({"input": str(path)}, audio, q)
        log = "".join(m[1] for m in list(q.queue) if m[0] == "log")
        return turns, log

    def test_channels_win_and_no_voiceprint_model_is_touched(self):
        s = self.sig
        path = _write_wav(self.tmp / "x.wav", [
            s.a + 0.18 * s.b + s.noise(), s.b + 0.18 * s.a + s.noise()])
        with mock.patch.object(D, "_run_diarization") as vp, \
                mock.patch.object(D, "ensure_diarize_models") as dl:
            turns, log = self.run_it(path)
        vp.assert_not_called()
        dl.assert_not_called()
        self.assertIn("separate channels", log)
        self.assertEqual({c for *_, c in turns}, {0, 1})

    def test_falls_back_to_voiceprints(self):
        s = self.sig
        mix = s.a + s.b + s.noise()
        path = _write_wav(self.tmp / "x.wav", [mix, mix])
        with mock.patch.object(D, "_run_diarization",
                               return_value=[(0.0, 1.0, 0)]) as vp, \
                mock.patch.object(D, "diarize_models_ready",
                                  return_value=True):
            turns, log = self.run_it(path, audio=np.zeros(SR, np.float32))
        vp.assert_called_once()
        self.assertEqual(turns, [(0.0, 1.0, 0)])
        self.assertNotIn("separate channels", log)

    def test_no_audio_and_no_channels_is_unavailable(self):
        s = self.sig
        path = _write_wav(self.tmp / "x.wav", [s.a + s.b + s.noise()])
        with self.assertRaises(T.DiarizationUnavailable):
            self.run_it(path, audio=None)

    def test_a_stop_is_honoured(self):
        import queue
        import threading
        s = self.sig
        path = _write_wav(self.tmp / "x.wav", [
            s.a + 0.18 * s.b + s.noise(), s.b + 0.18 * s.a + s.noise()])
        stop = threading.Event()
        stop.set()
        with self.assertRaises(T._CancelledByUser):
            D.speaker_turns({"input": str(path)}, None, queue.Queue(), stop)


if __name__ == "__main__":
    unittest.main()

"""Who is speaking, without a voiceprint: cues that a new person has
the floor, read from the words and from simple measurements of the
voice.

Voiceprint diarization needs a model trained on other people's voices
and is the less reliable half of speaker detection. Much of what a
human transcriber uses to tell speakers apart needs no such model: a
question in "you" answered in "I", "Your Honour" at the start of a
sentence, the caution in an ERISP, a jump in pitch from a man's voice
to a child's. This module measures those cues sentence by sentence. It
decides nothing by itself; the paragrapher weighs them.

Everything here is plain text processing and signal arithmetic -
deterministic, fast, and free of any trained model.
"""

import math
import re

SR = 16000
_FRAME = 640          # 40 ms analysis window
_HOP = 320            # 20 ms step
_FMIN, _FMAX = 70.0, 400.0


# ---- Sentences ---------------------------------------------------------

def sentences(segments, words):
    """[(start, end, text, [word tuples])] - the transcript cut at every
    sentence end, using word timings, so a speaker change inside an
    engine segment has a boundary to land on. Segments without word
    timings stay whole."""
    out = []
    wi = 0
    words = sorted(words or [], key=lambda w: w[0])
    for seg in segments:
        s0, s1, text = float(seg[0]), float(seg[1]), str(seg[2])
        mine = []
        while wi < len(words) and words[wi][0] < s1 - 0.01:
            if words[wi][1] > s0 - 0.05:
                mine.append(words[wi])
            wi += 1
        if not mine:
            if text.strip():
                out.append((s0, s1, text.strip(), []))
            continue
        cur = []
        for w in mine:
            cur.append(w)
            if str(w[2]).rstrip().endswith((".", "?", "!")):
                out.append(_sentence(cur))
                cur = []
        if cur:
            out.append(_sentence(cur))
    return out


def _sentence(ws):
    return (float(ws[0][0]), float(ws[-1][1]),
            "".join(str(w[2]) for w in ws).strip(), list(ws))


# ---- Text cues ---------------------------------------------------------

_YOU = re.compile(r"\b(you|your|you're|you've|you'd|you'll|yourself)\b", re.I)
_I_OPEN = re.compile(r"^\W*(i|i'm|i've|i'd|i'll|my|me|we|we're|we've)\b", re.I)
_ADDRESS = re.compile(
    r"\b(your honou?r|your worship|officer|constable|detective|sergeant|"
    r"sir|ma'am|madam|mr|mrs|ms|miss|dr|mate)\b", re.I)
_ANSWER_OPEN = re.compile(
    r"^\W*(yes|yeah|yep|no|nope|nah|because|cause|'cause|i don't know|"
    r"i dunno|i can't remember|i don't remember|not really|i think|"
    r"i guess|probably|maybe|well|um|uh|correct|that's right|that's "
    r"correct|sort of|kind of)\b", re.I)
_TAG_Q = re.compile(
    r",\s*(didn't|don't|doesn't|isn't|wasn't|weren't|aren't|haven't|"
    r"hasn't|won't|wouldn't|couldn't|can't|shouldn't|did|do|is|was|are|"
    r"have|will|would|could)\s+(you|he|she|it|they|we|i|there)\s*\?$|"
    r",\s*(right|correct|yeah|okay|ok|eh|agreed|true)\s*\?$", re.I)
_FORMULA = re.compile(
    r"\b(for the purposes of the (recording|record|tape)|"
    r"do you agree (that|with)|is that (correct|right|true)|"
    r"you are not obliged|anything you say|do you understand|"
    r"the time (now )?is|this interview|electronically recorded|"
    r"i('m| am) going to ask you|can you tell me|tell me about|"
    r"what happened (next|then)|in your own words|"
    r"i put it to you|i suggest to you|would you agree)\b", re.I)
_DASH_OPEN = re.compile(r"^\s*[-–—]\s*\S")


def text_cues(prev, curr):
    """Cues at the boundary between sentence texts `prev` and `curr`,
    each 0 or 1. Positive values all point to a change of speaker."""
    p, c = prev.strip(), curr.strip()
    p_words = re.findall(r"[\w']+", p.lower())
    c_words = re.findall(r"[\w']+", c.lower())
    echo = (c.endswith("?") and 0 < len(c_words) <= 3
            and any(w in p_words for w in c_words if len(w) > 2))
    return {
        "question": int(p.endswith("?")),
        # after an answer, a new question usually comes from the other
        # side
        "question_curr": int(c.endswith("?") and not p.endswith("?")),
        "cut_off": int(p.endswith(("--", "—", "–", "...", "…"))),
        "dash_open": int(bool(_DASH_OPEN.match(c))),
        "you_to_i": int(bool(_YOU.search(p)) and bool(_I_OPEN.match(c))),
        "address_open": int(bool(_ADDRESS.match(c.lstrip(" ,.-—")))),
        "address_close": int(bool(re.search(
            r",\s*" + _ADDRESS.pattern + r"\W*$", p, re.I))),
        "answer_open": int(bool(_ANSWER_OPEN.match(c))),
        "short_reply": int(0 < len(c_words) <= 3),
        "echo": int(bool(echo)),
        "tag_question": int(bool(_TAG_Q.search(p))),
        "formula_prev": int(bool(_FORMULA.search(p))),
        "formula_curr": int(bool(_FORMULA.search(c))),
    }


def asks(text):
    """How question-like, interviewer-like a sentence is (for roles)."""
    t = text.strip()
    return int(t.endswith("?")) + int(bool(_FORMULA.search(t))) + \
        int(bool(_TAG_Q.search(t)))


def answers(text):
    """How answer-like, interviewee-like a sentence is (for roles)."""
    t = text.strip()
    return int(bool(_ANSWER_OPEN.match(t))) + int(bool(_I_OPEN.match(t)))


# ---- Voice measurements ------------------------------------------------

def voice_track(audio):
    """Per 20 ms frame: (pitch in Hz or 0 where unvoiced, level in dB).
    Pitch by normalised autocorrelation - plain arithmetic, no model."""
    import numpy as np
    a = np.asarray(audio, dtype=np.float32)
    n = 1 + max(0, (len(a) - _FRAME) // _HOP)
    if n <= 1:
        return np.zeros(0), np.zeros(0)
    idx = np.arange(_FRAME)[None, :] + _HOP * np.arange(n)[:, None]
    pitch = np.zeros(n, dtype=np.float32)
    level = np.zeros(n, dtype=np.float32)
    lo, hi = int(SR / _FMAX), int(SR / _FMIN)
    win = np.hanning(_FRAME).astype(np.float32)
    for start in range(0, n, 4096):          # bounded memory
        fr = a[idx[start:start + 4096]] * win
        fr -= fr.mean(axis=1, keepdims=True)
        energy = (fr ** 2).mean(axis=1)
        level[start:start + len(fr)] = 10 * np.log10(energy + 1e-10)
        spec = np.fft.rfft(fr, 2 * _FRAME)
        ac = np.fft.irfft(np.abs(spec) ** 2)[:, :hi + 1]
        ac /= ac[:, :1] + 1e-10
        lag = lo + np.argmax(ac[:, lo:hi + 1], axis=1)
        strength = ac[np.arange(len(fr)), lag]
        voiced = (strength > 0.45) & (energy > 1e-6)
        pitch[start:start + len(fr)] = np.where(voiced, SR / lag, 0.0)
    return pitch, level


def voice_of(track, start, end):
    """(median pitch Hz or None, median level dB or None) for a span."""
    import numpy as np
    pitch, level = track
    a, b = int(start * SR / _HOP), int(end * SR / _HOP) + 1
    p = pitch[a:b]
    p = p[p > 0]
    lv = level[a:b]
    return (float(np.median(p)) if len(p) >= 5 else None,
            float(np.median(lv)) if len(lv) else None)


def voice_cues(track, prev, curr):
    """Voice differences across a boundary between sentences `prev` and
    `curr` (each (start, end, text, words)): pitch jump in semitones,
    level jump in dB, and speaking-rate ratio. None where unmeasurable."""
    import math
    p_hz, p_db = voice_of(track, prev[0], prev[1])
    c_hz, c_db = voice_of(track, curr[0], curr[1])

    def rate(s):
        d = s[1] - s[0]
        return len(s[2].split()) / d if d > 0.3 else None

    pr, cr = rate(prev), rate(curr)
    return {
        "pitch_jump": (abs(12 * math.log2(c_hz / p_hz))
                       if p_hz and c_hz else None),
        "level_jump": abs(c_db - p_db) if p_db is not None and
        c_db is not None else None,
        "rate_ratio": (max(pr, cr) / min(pr, cr)) if pr and cr else None,
    }


def boundary_features(sents, track=None):
    """One dict per boundary between consecutive sentences: the gap,
    every text cue, and (with a voice track) the voice cues."""
    out = []
    for a, b in zip(sents, sents[1:]):
        f = {"gap": max(0.0, b[0] - a[1])}
        f.update(text_cues(a[2], b[2]))
        if track is not None:
            f.update(voice_cues(track, a, b))
        out.append(f)
    return out


# ---- Putting the cues together ------------------------------------------

# Log-odds each cue adds to "the speaker changed here": a starting
# point set by hand.
PRIOR_WEIGHTS = {
    "bias": -2.2,
    "gap": 1.2,                 # per second, capped at 3 s
    "question": 1.6,
    "question_curr": 0.9,
    "cut_off": 0.5,
    "dash_open": 1.5,
    "you_to_i": 1.0,
    "address_open": 0.8,
    "address_close": 0.8,
    "answer_open": 0.4,
    "echo": 1.0,
    "tag_question": 1.2,
    "formula_prev": 0.4,
    "formula_curr": 0.4,
    "pitch_jump": 0.35,         # per semitone beyond 1.5
    "level_jump": 0.08,         # per dB beyond 5
}


# Paragraph boundaries are already drawn where a turn is likely, so the
# same cues mean a change more often there than between sentences.
# Provisional.
PARAGRAPH_BIAS_SHIFT = 1.2


def change_probability(features, weights=None, unit="sentence"):
    """P(the speaker changed) at each boundary, from its cues. `unit`
    is what the boundaries separate: "sentence" or "paragraph"."""
    import math
    w = weights or PRIOR_WEIGHTS
    shift = PARAGRAPH_BIAS_SHIFT if unit == "paragraph" else 0.0
    out = []
    for f in features:
        z = w.get("bias", 0.0) + shift \
            + w.get("gap", 0.0) * min(f["gap"], 3.0)
        for k, v in f.items():
            if k in ("gap", "pitch_jump", "level_jump", "rate_ratio"):
                continue
            z += w.get(k, 0.0) * v
        if f.get("pitch_jump") is not None:
            z += w.get("pitch_jump", 0.0) * max(0.0, f["pitch_jump"] - 1.5)
        if f.get("level_jump") is not None:
            z += w.get("level_jump", 0.0) * max(0.0, f["level_jump"] - 5.0)
        out.append(1 / (1 + math.exp(-z)))
    return out


def _two_voices(values):
    """Two-cluster split of log pitch values by EM: (means, sds, weights)
    or None when there are too few voiced sentences to tell."""
    import numpy as np
    x = np.asarray([v for v in values if v], dtype=float)
    if len(x) < 8:
        return None
    x = np.log2(x) * 12                     # semitones
    mu = np.percentile(x, [25, 75]).astype(float)
    sd = np.array([x.std() / 2 + 0.5] * 2)
    pi = np.array([0.5, 0.5])
    for _ in range(50):
        d = np.stack([pi[k] * np.exp(-0.5 * ((x - mu[k]) / sd[k]) ** 2)
                      / sd[k] for k in range(2)])
        r = d / (d.sum(0) + 1e-12)
        nk = r.sum(1) + 1e-9
        mu = (r * x).sum(1) / nk
        sd = np.sqrt((r * (x - mu[:, None]) ** 2).sum(1) / nk) + 0.3
        pi = nk / nk.sum()
    return mu, sd, pi


def two_party_roles(sents, track=None, features=None, weights=None,
                    unit="sentence"):
    """For a two-person interview: each sentence's role, 0 for the one
    asking (interviewer, questioner) and 1 for the one answering, with
    the probability of that role. Viterbi over the sentences, where a
    switch costs the boundary's change probability and each sentence
    is scored by how question- or answer-like it is and, where the two
    voices differ in pitch, by which voice it sounds like.

    Returns [(role, confidence)] - one per sentence."""
    import math
    import numpy as np
    n = len(sents)
    if n == 0:
        return []
    feats = features if features is not None else \
        boundary_features(sents, track)
    p_change = change_probability(feats, weights, unit=unit)

    # Text evidence per sentence: log P(text | role).
    emit = np.zeros((n, 2))
    for i, s in enumerate(sents):
        a, b = asks(s[2]), answers(s[2])
        emit[i, 0] += 1.2 * a - 0.6 * b
        emit[i, 1] += 1.2 * b - 0.6 * a

    # Voice evidence: if the sentences' pitches fall into two clusters,
    # each cluster is a voice; which voice asks is settled by the text.
    if track is not None:
        pitches = [voice_of(track, s[0], s[1])[0] for s in sents]
        fit = _two_voices(pitches)
        if fit is not None:
            mu, sd, pi = fit
            separation = abs(mu[0] - mu[1]) / math.sqrt(sd[0] * sd[1])
            if separation > 1.0:
                ll = np.zeros((n, 2))
                for i, hz in enumerate(pitches):
                    if hz:
                        st = math.log2(hz) * 12
                        ll[i] = [-0.5 * ((st - mu[k]) / sd[k]) ** 2
                                 - math.log(sd[k]) for k in range(2)]
                # which cluster is the asker: the one the question-like
                # sentences sound like
                q_like = np.array([emit[i, 0] - emit[i, 1] for i in range(n)])
                vote = float((q_like * (ll[:, 0] - ll[:, 1])).sum())
                if vote < 0:
                    ll = ll[:, ::-1]
                weight = min(1.0, separation / 3.0)
                emit += weight * (ll - ll.max(1, keepdims=True))

    # Viterbi: roles 0 and 1, switching at boundary i costs log p_change.
    score = np.zeros((n, 2))
    back = np.zeros((n, 2), dtype=int)
    score[0] = emit[0] + np.log([0.6, 0.4])   # interviews open with the asker
    for i in range(1, n):
        pc = min(max(p_change[i - 1], 1e-4), 1 - 1e-4)
        stay, switch = math.log(1 - pc), math.log(pc)
        for r in range(2):
            cands = (score[i - 1, r] + stay, score[i - 1, 1 - r] + switch)
            k = int(np.argmax(cands))
            back[i, r] = r if k == 0 else 1 - r
            score[i, r] = max(cands) + emit[i, r]
    roles = [0] * n
    roles[-1] = int(np.argmax(score[-1]))
    for i in range(n - 1, 0, -1):
        roles[i - 1] = int(back[i, roles[i]])
    # confidence: how decisive the local evidence is for the chosen role
    conf = []
    for i, r in enumerate(roles):
        d = emit[i, r] - emit[i, 1 - r]
        conf.append(round(1 / (1 + math.exp(-d)), 3))
    return list(zip(roles, conf))


class VoiceMeter:
    """Median voice pitch over any stretch of a recording, measured on
    demand and remembered - so the live paragrapher can ask about each
    segment as it arrives without analysing the whole recording first."""

    def __init__(self, audio=None, track=None):
        """From the samples, or from a (pitch, level) track already
        computed by voice_track()."""
        self.audio = audio
        self.track = track
        self._cache = {}

    def pitch(self, start, end):
        """Median pitch in Hz of the voiced frames in [start, end], or
        None when too little of it is voiced to say."""
        import numpy as np
        key = (round(start, 2), round(end, 2))
        if key in self._cache:
            return self._cache[key]
        hz = None
        if self.track is not None:
            p = self.track[0][int(start * SR / _HOP):int(end * SR / _HOP) + 1]
            voiced = p[p > 0]
        else:
            a = max(0, int(start * SR))
            b = min(len(self.audio), int(end * SR))
            voiced = []
            if b - a >= _FRAME * 3:
                pitch, _level = voice_track(np.asarray(self.audio[a:b]))
                voiced = pitch[pitch > 0]
        if len(voiced) >= 10:
            hz = float(np.median(voiced))
        self._cache[key] = hz
        return hz

    def jump(self, prev, curr, span=3.0):
        """Pitch change in semitones from the end of `prev` to the start
        of `curr` (each (start, end, ...)), or None. Only the `span`
        seconds either side of the boundary are compared: that is where
        a change of speaker shows, and a long segment's own drift would
        blur it."""
        p = self.pitch(max(prev[0], prev[1] - span), prev[1])
        c = self.pitch(curr[0], min(curr[1], curr[0] + span))
        if not p or not c:
            return None
        return abs(12.0 * math.log2(c / p))

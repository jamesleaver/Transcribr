"""Speakers on separate channels: notice it, and say who spoke when.

Court recording systems, phone-call recorders and conferencing exports
often give each microphone - and so, in practice, each speaker - a
channel or a track of its own. When they do, which channel is carrying
the speech is a far better guide to who is speaking than any voiceprint:
it does not merge two similar voices, does not invent speakers, and
needs no model download.

The whole of it rests on one measurement: per channel, how far above
that channel's own noise floor the signal is, frame by frame. Each
channel is judged against its own floor rather than its raw level
because recording systems run channels at very different gains, and a
hot channel would otherwise "win" every frame. Gain lifts speech and
noise alike, so height above the floor is unaffected by it.

Separate microphones show up as one channel standing clearly above the
rest whenever anybody speaks, with different channels taking turns.
Two things that also arrive as stereo must NOT be read that way, and
are rejected:
  - mono copied to both channels, where no channel ever stands out;
  - a stereo pair in a room, where every voice reaches both microphones
    and the difference between them stays within a few dB.

Only a loudness trace is kept (50 values a second per channel) - never
the samples - so an hour of eight channels costs a few megabytes.
"""

import time

from .platformutil import _log

SAMPLE_RATE = 16000
FRAME_S = 0.02
MAX_CHANNELS = 8

# A frame is speech on a channel when it stands this far above that
# channel's own noise floor.
_ACTIVE_DB = 12.0
# Channels count as separate microphones when, in speech, the leading
# channel typically stands at least this far clear of the next.
_DOMINANCE_DB = 6.0
# A channel is "in use" when it leads at least this share of speech.
_MIN_SHARE = 0.03
# While one channel leads, another counts as speaking at the same time
# (overlapping speech) only when within this much of the leader -
# further down, it is only the leader's voice bleeding into its mic.
_OVERLAP_DB = 6.0
# Enough speech to judge from, and the most audio read to judge it.
_JUDGE_SPEECH_S = 30.0
_JUDGE_MAX_S = 180.0
# Turn smoothing: bridge gaps shorter than this, drop blips shorter
# than that.
_BRIDGE_S = 0.3
_MIN_TURN_S = 0.25


def _db(ms):
    import numpy as np
    return 10.0 * np.log10(ms + 1e-12)


def _layout_channels(stream):
    try:
        return int(stream.codec_context.channels)
    except Exception:
        try:
            return len(stream.layout.channels)
        except Exception:
            return 1


def _levels_one_stream(path, index, keep_channels, cancel_event,
                       limit_s=None):
    """dB per frame for audio stream `index`: shape (channels, frames)
    when `keep_channels`, else the stream downmixed to one row."""
    import av
    import numpy as np

    hop = int(SAMPLE_RATE * FRAME_S)
    rows, pending = [], None
    with av.open(str(path)) as container:
        stream = container.streams.audio[index]
        layout = stream.codec_context.layout if keep_channels else "mono"
        rs = av.AudioResampler(format="fltp", layout=layout,
                               rate=SAMPLE_RATE)

        def take(block):
            nonlocal pending
            block = block if pending is None else np.concatenate(
                [pending, block], axis=1)
            n = block.shape[1] // hop
            if n:
                frames = block[:, :n * hop].reshape(block.shape[0], n, hop)
                rows.append(_db(np.mean(frames.astype(np.float64) ** 2,
                                        axis=2)))
            pending = block[:, n * hop:]

        done_s = 0.0
        for frame in container.decode(stream):
            for rf in rs.resample(frame):
                a = rf.to_ndarray()
                take(a.reshape(1, -1) if a.ndim == 1 else a)
                done_s += a.shape[-1] / SAMPLE_RATE
            if cancel_event is not None and cancel_event.is_set():
                return None
            if limit_s is not None and done_s >= limit_s:
                break
    if not rows:
        return None
    return np.concatenate(rows, axis=1)


def channel_levels(path, cancel_event=None, limit_s=None):
    """Per-channel loudness trace for `path`: (levels, frame_s), where
    levels is (channels, frames) in dB. Several channels in one stream
    and several audio tracks are both handled. None when there is only
    one channel and one track - decided from the header, so the common
    mono recording costs nothing - or when it cannot be read."""
    import av
    import numpy as np

    try:
        with av.open(str(path)) as container:
            streams = list(container.streams.audio)
            counts = [_layout_channels(s) for s in streams]
    except Exception:
        return None
    if not streams:
        return None
    try:
        if len(streams) == 1:
            if counts[0] < 2 or counts[0] > MAX_CHANNELS:
                return None
            levels = _levels_one_stream(path, 0, True, cancel_event,
                                        limit_s)
        else:
            if len(streams) > MAX_CHANNELS:
                return None
            tracks = []
            for i in range(len(streams)):
                t = _levels_one_stream(path, i, False, cancel_event,
                                       limit_s)
                if t is None:
                    return None
                tracks.append(t[0])
            n = max(len(t) for t in tracks)
            # A track that ends early is padded at its own noise floor,
            # not with digital silence: silence would drag the floor
            # down and make the whole track look falsely loud against it.
            levels = np.stack([np.pad(t, (0, n - len(t)),
                                      constant_values=float(
                                          np.percentile(t, 10)))
                               for t in tracks])
    except Exception:
        _log(f"channel analysis could not read {path}")
        return None
    if levels is None:
        return None
    return levels, FRAME_S


def _heights(levels):
    """dB above each channel's own noise floor (its 10th percentile)."""
    import numpy as np
    floor = np.percentile(levels, 10, axis=1)
    return levels - floor[:, None]


def judge(levels):
    """Do these channels carry separate speakers? Returns a dict:
    separated (bool), used (channel indices in use), speech_s,
    dominance_db (median lead of the leading channel in speech), and
    shares (share of speech each channel leads)."""
    import numpy as np

    h = _heights(levels)
    active = h.max(axis=0) > _ACTIVE_DB
    speech_s = float(active.sum() * FRAME_S)
    verdict = {"separated": False, "used": [], "speech_s": speech_s,
               "dominance_db": 0.0, "shares": []}
    if levels.shape[0] < 2 or active.sum() < int(5 / FRAME_S):
        return verdict
    ha = h[:, active]
    top = np.sort(ha, axis=0)
    lead = top[-1] - top[-2]
    leader = ha.argmax(axis=0)
    clear = lead >= _DOMINANCE_DB
    shares = np.bincount(leader[clear], minlength=levels.shape[0]) \
        / active.sum()
    used = [int(c) for c in np.flatnonzero(shares >= _MIN_SHARE)]
    dominance = float(np.median(lead))
    verdict.update(dominance_db=round(dominance, 1), used=used,
                   shares=[round(float(s), 3) for s in shares],
                   separated=bool(dominance >= _DOMINANCE_DB
                                  and len(used) >= 2))
    return verdict


def _runs(mask):
    """[(first, last+1)] index runs where mask is True."""
    import numpy as np
    m = np.concatenate([[False], mask, [False]]).astype(np.int8)
    d = np.diff(m)
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def channel_turns(levels, used):
    """[(start_s, end_s, channel)] for the channels in `used`, sorted by
    start. Two channels speaking at once produce overlapping turns,
    which is what labelling expects of overlapping speech."""
    import numpy as np

    h = _heights(levels)
    lead = h.max(axis=0)
    bridge = int(round(_BRIDGE_S / FRAME_S))
    shortest = int(round(_MIN_TURN_S / FRAME_S))
    turns = []
    for c in used:
        speaking = (h[c] > _ACTIVE_DB) & (h[c] >= lead - _OVERLAP_DB)
        runs = _runs(speaking)
        merged = []
        for a, b in runs:
            if merged and a - merged[-1][1] <= bridge:
                merged[-1] = (merged[-1][0], b)
            else:
                merged.append((a, b))
        turns += [(a * FRAME_S, b * FRAME_S, c) for a, b in merged
                  if b - a >= shortest]
    return sorted(turns)


def separated_channel_turns(path, cancel_event=None):
    """Turns by channel when `path` has its speakers on separate
    channels or tracks; otherwise None. Judges from the opening minutes
    first, so a stereo file that is really mono is dismissed without
    reading all of it."""
    t0 = time.time()
    head = channel_levels(path, cancel_event, limit_s=_JUDGE_MAX_S)
    if head is None:
        return None
    verdict = judge(head[0])
    # Not enough speech early on to judge fairly: judge the whole file.
    if not verdict["separated"] and verdict["speech_s"] >= _JUDGE_SPEECH_S:
        return None
    full = channel_levels(path, cancel_event)
    if full is None:
        return None
    levels = full[0]
    verdict = judge(levels)
    if not verdict["separated"]:
        return None
    turns = channel_turns(levels, verdict["used"])
    verdict["seconds"] = round(time.time() - t0, 1)
    verdict["channels"] = int(levels.shape[0])
    return turns, verdict

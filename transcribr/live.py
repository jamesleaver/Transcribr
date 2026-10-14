"""Assembling a transcript while the engine is still producing it.

Three things have to happen to the engine's segments before they are a
transcript, and all three have to happen in time order: text the model
invented inside dead air is dropped, the rest is grouped into
paragraphs, and a marker is woven in wherever the recording has no
audio at all.

Done at the end, as it was until 0.9.16, that is a single pass over
everything. Done as the segments arrive, it is the same pass with a
pointer into each input - which is what lets the review pane fill in
during the run instead of after it.

The rule the whole thing rests on: a paragraph is only handed over once
nothing still to come can change it. LiveTranscript emits paragraphs
that are final and keeps the one still being built to itself, so an
edit made in the pane can never be overwritten by a segment that
arrives a moment later.
"""

from .paragraphs import (ParagraphStream, _refine_segment_times, format_timestamp,
                         split_at_speaker_changes)


def no_audio_marker(start, end):
    """The paragraph that stands in for a stretch with no recording."""
    plain = lambda t: format_timestamp(t)[1:-1]     # noqa: E731
    return [(start, end, f"[No audio from {plain(start)} to {plain(end)}]")]


def segment_is_phantom(segment, spans):
    """Whether this segment lies (almost) entirely inside dead air.

    At least 90% overlap, so real speech brushing the edge of a span is
    never dropped. The same test _drop_segments_in_silence applies in
    the batch path, one segment at a time."""
    s, e = float(segment[0]), float(segment[1])
    length = max(e - s, 1e-6)
    return any((min(e, b) - max(s, a)) / length >= 0.9 for a, b in spans)


class LiveTranscript:
    """Turns segments into finished paragraphs as they arrive.

    push() returns the paragraphs that are now final, dead-air markers
    included and in time order. The paragraph still being built stays
    behind `tail` until something seals it.
    """

    def __init__(self, gap_threshold, *, silence_spans=(),
                 gaps_informative=None, turns=None, voice=None):
        self.spans = sorted((float(a), float(b)) for a, b in silence_spans)
        self.dropped = 0
        self.turns = turns
        self._stream = ParagraphStream(gap_threshold,
                                       gaps_informative=gaps_informative,
                                       turns=turns, voice=voice)
        self._next_marker = 0
        self._emitted = 0

    @property
    def tail(self):
        """The paragraph still open, or None."""
        return self._stream.tail

    @property
    def paragraph_count(self):
        """How many paragraphs have been handed over so far."""
        return self._emitted

    def push(self, segments, words=None):
        """Feed in newly transcribed segments. `words` are the word
        timings for exactly these segments, when the engine recorded
        them - they sharpen the gaps the paragraph breaks are measured
        from, the same way the batch path uses them."""
        usable = []
        for seg in segments:
            if segment_is_phantom(seg, self.spans):
                self.dropped += 1
                continue
            usable.append(seg)
        if not usable:
            return []
        if words:
            usable = _refine_segment_times(usable, words)
            if self.turns:
                usable = split_at_speaker_changes(usable, words, self.turns)
        return self._with_markers(self._stream.push(usable))

    def finish(self):
        """The run is over: close the open paragraph and emit any
        markers that belong after it."""
        out = self._with_markers(self._stream.finish())
        trailing = self._markers_before(None)
        out.extend(trailing)
        self._emitted += len(trailing)
        return out

    def _with_markers(self, paragraphs):
        """Interleave pending dead-air markers with newly sealed
        paragraphs, in time order - a marker goes first when its span
        starts at or before the paragraph does, matching the batch
        path's comparison exactly."""
        out = []
        for para in paragraphs:
            out.extend(self._markers_before(para[0][0]))
            out.append(para)
        self._emitted += len(out)
        return out

    def _markers_before(self, start):
        """Markers whose span starts at or before `start` (all of the
        remaining ones when `start` is None)."""
        out = []
        while self._next_marker < len(self.spans):
            a, b = self.spans[self._next_marker]
            if start is not None and a > start:
                break
            out.append(no_audio_marker(a, b))
            self._next_marker += 1
        return out

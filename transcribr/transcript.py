"""The headless transcript model the review pane edits."""

from pathlib import Path
import re
import threading

from .errors import _EngineNotAvailable
from .speakers import MAX_SPEAKERS, SPEAKER_SLOTS
from .platformutil import _terms_load
from .paragraphs import get_audio_duration
from .engines import LANGUAGES, available_engines, resolve_engine_key
from .diarize import DIARIZE_DEFAULT_VOICE_MODEL, _DIARIZE_CLUSTER_THRESHOLD


#
# The review pane's document logic with no widgets: paragraphs (lists
# of (start, end, text) segment tuples), speaker slots "1".."9",
# snapshot-based undo/redo, split/merge/edit/replace-all, playback
# spans, confidence spans, and the debounced autosave trigger. The web
# review surface drives one of these over the API; ReviewPaneText keeps
# its own fused copy of the same logic until the Tk UI is retired.
#
# Semantics are ported 1:1 from ReviewPaneText - where behaviour looks
# arbitrary (the +0.3s playback tail, bailing out of confidence shading
# on alignment drift, empty edit meaning cancel) it is deliberate and
# matches what the Tk pane does today.

class TranscriptModel:
    MAX_SPEAKERS = MAX_SPEAKERS
    DEFAULT_VISIBLE = 4
    LETTERS = SPEAKER_SLOTS
    _UNDO_LIMIT = 200

    # How long after a change the crash-recovery snapshot is written:
    # a short debounce for human editing, and a longer ceiling for a
    # run streaming paragraphs in (see _schedule_autosave).
    AUTOSAVE_DEBOUNCE = 3.0
    AUTOSAVE_GROWTH = 15.0

    # Every list that has to stay exactly parallel to self.paragraphs,
    # and what a newly created paragraph gets in each. Splices go
    # through _splice_parallel / _drop_parallel rather than editing the
    # lists one at a time, because six lists kept in step by hand is six
    # chances to forget one.
    _PARALLEL_DEFAULTS = {
        "speakers": None,
        "ts_marks": None,
        "text_origin": "engine",
        "speaker_origin": None,
    }

    def __init__(self, paragraphs, speakers=None, speaker_names=None,
                 word_conf=None, ts_marks=None, *,
                 on_autosave=None, timer_factory=None,
                 has_timings=True, text_origin=None, speaker_origin=None,
                 suggestions=None, marked=None):
        # False when the paragraphs carry no real times at all - a
        # transcript saved without timestamps parses back with every
        # start at 0.0, which used to make every paragraph "play" from
        # the beginning of the recording.
        self.has_timings = bool(has_timings)
        self.paragraphs = [list(p) for p in paragraphs]
        n = len(self.paragraphs)
        self.speakers = (list(speakers) if speakers is not None
                         else [None] * n)
        self.speaker_names = {letter: f"Speaker {letter}"
                              for letter in self.LETTERS}
        if speaker_names:
            self.speaker_names.update(speaker_names)
        self.word_conf = word_conf
        self.show_confidence = False
        # (paragraph id, text) of passages the reviewer chose to keep
        # rather than mark [indistinct]; not offered again.
        self.indistinct_kept = set()
        used = [int(s) for s in self.speakers if s]
        self.visible_speakers = max(self.DEFAULT_VISIBLE,
                                    min(max(used, default=0),
                                        self.MAX_SPEAKERS))
        self._next_id = 0
        self.ids = [self._new_id() for _ in self.paragraphs]
        # Per-paragraph timestamp state: None = show the computed time,
        # "hidden" = omit the stamp in saved output, a number = show
        # that time instead (an editorial correction; playback keeps
        # using the real segment times).
        self.ts_marks = list(ts_marks) if ts_marks else [None] * n
        while len(self.ts_marks) < n:
            self.ts_marks.append(None)

        # Who last wrote each paragraph. "engine" until a person edits
        # it, then "user" - and an automated writer must never silently
        # replace "user" text. This is what makes it safe for the
        # engine to go on working on a transcript somebody is already
        # correcting: the repair pass, the speaker detector and the
        # streaming tail all check it before they touch anything.
        self.text_origin = self._fit(text_origin, n, "engine")
        self.speaker_origin = self._fit(speaker_origin, n, None)

        # Engine text for a paragraph the user has already edited: kept
        # as an offer rather than applied. Keyed by the paragraph's id
        # because indices shift and ids do not.
        self.suggestions = dict(suggestions or {})
        # What each accepted [indistinct] replaced, per paragraph id, in
        # the order the markers appear: so the reviewer can see the words
        # and put them back. None where they are not known. `marked`
        # arrives keyed by paragraph index (a reopened document).
        self.marked = {}
        for i, words in (marked or {}).items():
            i = int(i)
            if 0 <= i < len(self.ids):
                self.marked[self.ids[i]] = list(words)

        # How much of the transcript is final. While a run is still
        # going, paragraphs from here on are the engine's provisional
        # tail: it may still extend or re-group them as more segments
        # arrive, so they are not editable yet. A finished or loaded
        # transcript is sealed all the way to the end.
        self.sealed_count = n
        # How many paragraphs the engine has handed over as sealed, ever.
        # Monotonic, so the difference between two readings is exactly
        # how much the document grew in between - which is what lets an
        # undo snapshot find its own prefix again after a run has moved
        # on underneath it. See _restore.
        self._grown = 0

        self.rev = 0
        # The rev as at the last change a person could have been looking
        # at. Growth bumps `rev` (clients must refetch) but not this, so
        # an edit composed against a slightly older rev is still safe to
        # accept: the engine only ever appends past sealed_count, and a
        # person can only ever edit before it. Without the distinction,
        # every segment invalidated every in-flight edit.
        self.edit_rev = 0
        self._undo_stack = []
        self._redo_stack = []
        self.on_autosave = on_autosave
        self._timer_factory = timer_factory or (
            lambda delay, fn: threading.Timer(delay, fn))
        self._autosave_timer = None

    def _bump(self, *, edit=True):
        """Record a change. `edit=False` for the engine extending the
        transcript, which no client edit needs to be invalidated by."""
        self.rev += 1
        if edit:
            self.edit_rev = self.rev
        return self.rev

    def _new_id(self):
        self._next_id += 1
        return self._next_id

    @staticmethod
    def _fit(values, n, default):
        """A list of exactly n entries, padded with `default`."""
        out = list(values) if values else []
        del out[n:]
        out.extend([default] * (n - len(out)))
        return out

    def _drop_parallel(self, idx):
        """Remove paragraph idx's entry from every parallel list."""
        del self.ids[idx]
        for name in self._PARALLEL_DEFAULTS:
            del getattr(self, name)[idx]

    def _splice_parallel(self, lo, hi, count):
        """Replace [lo:hi] in every parallel list with `count` fresh
        entries, and hand out new ids for them."""
        self.ids[lo:hi + 1] = [self._new_id() for _ in range(count)]
        for name, default in self._PARALLEL_DEFAULTS.items():
            getattr(self, name)[lo:hi + 1] = [default] * count

    def _seal_shift(self, idx, delta):
        """Keep sealed_count on the same boundary after a structural
        edit at `idx` that changed the paragraph count by `delta`.

        Everything a person may edit lies before the boundary, so an
        edit that adds or removes paragraphs there moves it. Leaving it
        behind was what let a split during a run push a real paragraph
        into the engine's provisional tail - where the next grow
        overwrote it - and a delete pull the tail into the sealed body,
        where a half-finished paragraph stayed for good."""
        if idx < self.sealed_count:
            self.sealed_count = max(0, self.sealed_count + delta)

    def _insert_parallel(self, idx, **values):
        """Insert one entry at idx in every parallel list, taking each
        value from `values` or the default."""
        self.ids.insert(idx, self._new_id())
        for name, default in self._PARALLEL_DEFAULTS.items():
            getattr(self, name).insert(idx, values.get(name, default))

    # ----- Queries ----------------------------------------------------

    def body(self, idx):
        """The paragraph's rendered text - exactly what the client
        displays and what split()'s character offset indexes into."""
        return " ".join(seg[2] for seg in self.paragraphs[idx]).strip()

    def can_undo(self):
        return bool(self._undo_stack)

    def can_redo(self):
        return bool(self._redo_stack)

    def touch_rev(self):
        """Bump the revision without changing content - so a metadata
        change (e.g. a paragraph being locked for re-transcription)
        prompts clients to refetch the payload."""
        self._bump()
        return self.rev

    def label_counts(self):
        labelled = sum(1 for s in self.speakers if s is not None)
        return labelled, len(self.speakers)

    def resolved_speakers(self):
        """Slot letters -> stripped display names (None stays None)."""
        return [self.speaker_names.get(letter, "").strip() or None
                if letter else None
                for letter in self.speakers]

    def playback_span(self, idx):
        """(start_seconds, duration_or_None) covering paragraph `idx`,
        or None if there's nothing to play. Loaded transcripts only
        know start times (the parsers synthesise ~1s spans), so a span
        that still looks like a placeholder plays through to the next
        paragraph's start instead - or open-ended for the last one."""
        if not (0 <= idx < len(self.paragraphs)):
            return None
        if not self.has_timings:
            return None
        para = self.paragraphs[idx]
        if not para:
            return None
        start = max(0.0, float(para[0][0]))
        end = float(para[-1][1])
        if end - start <= 1.0:
            # Saved transcripts carry whole-second stamps, so runs of
            # consecutive paragraphs routinely share one start. Scan on
            # for the next paragraph that really does begin later:
            # consulting only the immediate neighbour made every
            # duplicated stamp play open-ended, i.e. "Play paragraph"
            # ran to the end of the recording instead of stopping.
            for nxt in self.paragraphs[idx + 1:]:
                if not nxt:
                    continue
                next_start = float(nxt[0][0])
                if next_start > start:
                    return (start, max(0.5, next_start - start + 0.3))
            return (start, None)
        # A touch of tail padding so the last word isn't clipped.
        return (start, max(0.5, end - start + 0.3))

    def _bucket_words_by_paragraph(self):
        """Partition word_conf into one [(word, prob)] list per
        paragraph by end time; single forward pass (both sides are
        time-ordered)."""
        buckets = [[] for _ in self.paragraphs]
        words = self.word_conf or []
        wi = 0
        n_para = len(self.paragraphs)
        for i, para in enumerate(self.paragraphs):
            if not para:
                continue
            p_end = para[-1][1]
            is_last = (i == n_para - 1)
            while wi < len(words):
                if not is_last and words[wi][0] >= p_end:
                    break
                buckets[i].append((words[wi][2], words[wi][3]))
                wi += 1
        return buckets

    INDISTINCT = "[indistinct]"
    # Below this a word is "low" confidence (shaded red in review); a
    # run of two or more low words, or one word below the second
    # threshold, is offered for marking as [indistinct].
    _LOW, _MED, _VERY_LOW = 0.35, 0.6, 0.15

    def _aligned_words(self, i, words_i):
        """[(start_char, end_char, prob)] for paragraph i's words in
        body() coordinates, or None when the text no longer aligns with
        the captured words.

        An accepted [indistinct] stands in for the words it replaced:
        while the body at the cursor is that marker, a word that does
        not appear straight after it is taken to be one of those it
        replaced and skipped, so the words beyond it still line up."""
        body = self.body(i)
        out, cursor = [], 0
        marker = self.INDISTINCT
        for wtext, prob in words_i:
            token = (wtext or "").strip()
            if not token:
                continue
            rest = body[cursor:]
            lead = len(rest) - len(rest.lstrip(" ,.;:!?-"))
            if rest[lead:].startswith(marker):
                after = cursor + lead + len(marker)
                tail = body[after:]
                skip = len(tail) - len(tail.lstrip(" ,.;:!?-"))
                if not tail[skip:].startswith(token):
                    continue            # one of the replaced words
                cursor = after
            pos = body.find(token, cursor)
            if pos < 0:
                return None
            cursor = pos + len(token)
            out.append((pos, cursor, prob))
        return out

    def confidence_spans(self):
        """Per paragraph: [(start_char, end_char, "low"|"med"), ...] in
        body() coordinates. A paragraph whose text no longer aligns
        with the captured words gets an empty list - never a wrong
        highlight."""
        if not self.word_conf:
            return [[] for _ in self.paragraphs]
        result = []
        for i, words_i in enumerate(self._bucket_words_by_paragraph()):
            spans = []
            for pos, end, prob in self._aligned_words(i, words_i) or []:
                if prob is None:
                    continue
                if prob < self._LOW:
                    spans.append((pos, end, "low"))
                elif prob < self._MED:
                    spans.append((pos, end, "med"))
            result.append(spans)
        return result

    def indistinct_suggestions(self):
        """Per paragraph: [(start_char, end_char)] passages to offer as
        [indistinct] - a run of two or more low-confidence words with
        nothing but spacing and punctuation between them, or a single
        word the engine was very unsure of. Never offered in a
        paragraph still being transcribed, and a passage the reviewer
        chose to keep is not offered again."""
        if not self.word_conf:
            return [[] for _ in self.paragraphs]
        dismissed = self.indistinct_kept
        result = []
        for i, words_i in enumerate(self._bucket_words_by_paragraph()):
            spans = []
            words = (self._aligned_words(i, words_i)
                     if i < self.sealed_count else None) or []
            body = self.body(i)
            run = []

            def close():
                if run and (len(run) >= 2
                            or min(p for _, _, p in run) < self._VERY_LOW):
                    a, b = run[0][0], run[-1][1]
                    if (self.ids[i], body[a:b]) not in dismissed:
                        spans.append((a, b))

            for w in words:
                pos, end, prob = w
                low = prob is not None and prob < self._LOW
                joined = (run and not body[run[-1][1]:pos].strip(" ,.;:!?-"))
                if low and (not run or joined):
                    run.append(w)
                    continue
                close()
                run = [w] if low else []
            close()
            result.append(spans)
        return result

    def markers_known(self, idx, count=None):
        """What each [indistinct] in paragraph idx replaced, in order,
        None where unknown. If the record no longer matches the markers
        in the text, every entry is unknown - pairing a marker with the
        wrong words would be worse than showing none."""
        n = self.body(idx).count(self.INDISTINCT) if count is None else count
        words = self.marked.get(self.ids[idx], [])
        return list(words) if len(words) == n else [None] * n

    def marker_spans(self, idx):
        """[(start_char, end_char, words, ordinal)] for each [indistinct]
        in paragraph idx whose words are known - the ones hovering can
        show and Restore can put back. Markers typed by hand, or left by
        a hand edit, are plain text and not listed."""
        body = self.body(idx)
        out, pos = [], 0
        for k, w in enumerate(self.markers_known(idx)):
            pos = body.find(self.INDISTINCT, pos)
            if w is not None:
                out.append((pos, pos + len(self.INDISTINCT), w, k))
            pos += len(self.INDISTINCT)
        return out

    def _replace_span(self, idx, start, end, text):
        """Replace body()[start:end] of paragraph idx with `text`, keeping
        the paragraph's segments (and so its timings) where the span lies
        inside one; a span across segments joins just those. The caller
        has checked the offsets and pushed the undo step."""
        para = self.paragraphs[idx]
        # Segment k covers [starts[k], starts[k] + len(text)) of the
        # joined body (segments are joined with single spaces).
        joined = " ".join(seg[2] for seg in para)
        lead = len(joined) - len(joined.lstrip())
        a, b = start + lead, end + lead
        starts, run = [], 0
        for seg in para:
            starts.append(run)
            run += len(seg[2]) + 1
        k1 = max(k for k in range(len(para)) if starts[k] <= a)
        k2 = max(k for k in range(len(para)) if starts[k] < b)
        first, last = para[k1], para[k2]
        new = (first[2][:a - starts[k1]] + text
               + last[2][b - starts[k2]:])
        self.paragraphs[idx] = (list(para[:k1])
                                + [(first[0], last[1], new)]
                                + list(para[k2 + 1:]))

    def mark_indistinct(self, idx, start, end, expect=None):
        """Replace body()[start:end] of paragraph idx with [indistinct],
        remembering the words so they can be shown and put back. One
        undo step. False if refused - including when `expect` (the words
        the reviewer was shown) is no longer what sits at those
        offsets."""
        if not (0 <= idx < len(self.paragraphs)) or not self._editable(idx):
            return False
        body = self.body(idx)
        if not (0 <= start < end <= len(body)):
            return False
        if expect is not None and body[start:end] != expect:
            return False
        words = self.markers_known(idx)
        words.insert(body[:start].count(self.INDISTINCT), body[start:end])
        self._push_undo()
        self._replace_span(idx, start, end, self.INDISTINCT)
        self.marked[self.ids[idx]] = words
        self.text_origin[idx] = "user"
        self._bump()
        return True

    def restore_indistinct(self, idx, ordinal, expect=None):
        """Put back the words the ordinal-th [indistinct] in paragraph
        idx replaced. One undo step. False if they are not known, or
        are not `expect` (what the reviewer was shown)."""
        if not (0 <= idx < len(self.paragraphs)) or not self._editable(idx):
            return False
        known = self.markers_known(idx)
        hit = [s for s in self.marker_spans(idx) if s[3] == ordinal]
        if not hit:
            return False
        a, b, words, _ = hit[0]
        if expect is not None and words != expect:
            return False
        kept = [w for k, w in enumerate(known) if k != ordinal]
        self._push_undo()
        self._replace_span(idx, a, b, words)
        self.marked[self.ids[idx]] = kept
        self.text_origin[idx] = "user"
        self._bump()
        return True

    def keep_passage(self, idx, start, end):
        """The reviewer kept a passage offered as [indistinct]: do not
        offer it again (by paragraph identity and text, which survive
        edits elsewhere)."""
        if not (0 <= idx < len(self.paragraphs)):
            return False
        body = self.body(idx)
        if not (0 <= start < end <= len(body)):
            return False
        self.indistinct_kept.add((self.ids[idx], body[start:end]))
        self.touch_rev()
        return True

    def attention_flags(self):
        """True per paragraph when it still needs attention: no speaker,
        or (with confidence shading on) a word below 0.6."""
        buckets = (self._bucket_words_by_paragraph()
                   if (self.show_confidence and self.word_conf) else None)
        flags = []
        for i, letter in enumerate(self.speakers):
            if letter is None:
                flags.append(True)
            elif buckets is not None:
                flags.append(any(p is not None and p < 0.6
                                 for _, p in buckets[i]))
            else:
                flags.append(False)
        return flags

    def _time_at_body_offset(self, para, joined, offset):
        """Start time of the word at/after character `offset` in
        `joined`, from the engine's word timestamps; None when word
        data is missing or no longer aligns with the edited text."""
        if not self.word_conf or not para:
            return None
        p_start = para[0][0]
        p_end = para[-1][1]
        cursor = 0
        for w_start, _w_end, w_text, _prob in self.word_conf:
            if w_start < p_start - 0.001:
                continue
            if w_start > p_end + 0.001:
                break
            token = (w_text or "").strip()
            if not token:
                continue
            pos = joined.find(token, cursor)
            if pos < 0:
                return None
            if pos >= offset:
                return float(w_start)
            cursor = pos + len(token)
        return None

    # ----- Undo / redo / autosave --------------------------------------

    def _snapshot(self):
        """The sealed part of the document - the only part a person can
        have changed - plus the document-wide state.

        The engine's provisional tail is deliberately left out. During a
        run the transcript keeps growing after a snapshot is taken, and
        a whole-document copy would roll all of that back: one undo used
        to discard every paragraph that had streamed in since the last
        edit, unrecoverably, because extend() only ever appends from
        sealed_count onward. `grown` records how much the engine had
        handed over at the time, which is what lets _restore find where
        this prefix now ends."""
        s = self.sealed_count
        snap = {
            "sealed_count": s,
            "grown": self._grown,
            "paragraphs": [list(p) for p in self.paragraphs[:s]],
            "ids": list(self.ids[:s]),
            "speaker_names": dict(self.speaker_names),
            "visible_speakers": self.visible_speakers,
            "suggestions": {k: dict(v) for k, v in self.suggestions.items()},
            "marked": {k: list(v) for k, v in self.marked.items()},
        }
        for name in self._PARALLEL_DEFAULTS:
            snap[name] = list(getattr(self, name)[:s])
        return snap

    def _restore(self, snap):
        # Where this snapshot's prefix ends in the document as it now
        # stands: the sealed boundary, less whatever the engine has
        # appended since. Everything from there on is the run's work and
        # is left exactly where it is.
        boundary = self.sealed_count - (self._grown - snap["grown"])
        boundary = max(0, min(boundary, len(self.paragraphs)))
        restored = len(snap["paragraphs"])
        self.paragraphs[:boundary] = [list(p) for p in snap["paragraphs"]]
        self.ids[:boundary] = list(snap["ids"])
        for name in self._PARALLEL_DEFAULTS:
            getattr(self, name)[:boundary] = list(snap[name])
        self.sealed_count += restored - boundary
        self.speaker_names = dict(snap["speaker_names"])
        self.visible_speakers = snap["visible_speakers"]
        self.suggestions = {k: dict(v)
                            for k, v in snap["suggestions"].items()}
        self.marked = {k: list(v) for k, v in snap.get("marked", {}).items()}

    def _push_undo(self):
        self._undo_stack.append(self._snapshot())
        if len(self._undo_stack) > self._UNDO_LIMIT:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        # Every mutation passes through here, so it doubles as the
        # autosave trigger (as in the Tk pane).
        self._schedule_autosave()

    def undo(self):
        if not self._undo_stack:
            return False
        self._redo_stack.append(self._snapshot())
        self._restore(self._undo_stack.pop())
        self._bump()
        self._schedule_autosave()
        return True

    def redo(self):
        if not self._redo_stack:
            return False
        self._undo_stack.append(self._snapshot())
        self._restore(self._redo_stack.pop())
        self._bump()
        self._schedule_autosave()
        return True

    def _schedule_autosave(self, *, growth=False):
        """Debounced crash-recovery snapshot: fires 3s after the last
        mutation rather than on every keystroke of a rename.

        Growth does not get the debounce. Segments arrive far more often
        than every three seconds, so a run rescheduling the timer on
        each one meant it never fired at all - no autosave for the whole
        length of a recording, which is exactly the stretch the live
        pane invites somebody to do their correcting in. Growth instead
        makes sure a save is pending and then leaves it alone, so one
        lands every AUTOSAVE_GROWTH seconds however fast the engine is
        going. A human edit still cancels it and takes the shorter
        debounce, because sooner is never wrong."""
        if self.on_autosave is None:
            return
        if growth:
            if self._autosave_timer is not None:
                return
            delay = self.AUTOSAVE_GROWTH
        else:
            if self._autosave_timer is not None:
                self._autosave_timer.cancel()
            delay = self.AUTOSAVE_DEBOUNCE
        t = self._timer_factory(delay, self._fire_autosave)
        if hasattr(t, "daemon"):
            t.daemon = True
        t.start()
        self._autosave_timer = t

    def _fire_autosave(self):
        self._autosave_timer = None
        if self.on_autosave is not None:
            self.on_autosave(self.paragraphs, self.speakers,
                             dict(self.speaker_names))

    def flush_autosave(self):
        """Run any pending autosave immediately (shutdown, tests)."""
        if self._autosave_timer is not None:
            self._autosave_timer.cancel()
            self._fire_autosave()

    def close(self):
        if self._autosave_timer is not None:
            self._autosave_timer.cancel()
            self._autosave_timer = None

    # ----- Mutations ----------------------------------------------------

    def set_speaker(self, idx, letter):
        """Assign slot `letter` ("1".."9") or None to paragraph idx."""
        if not (0 <= idx < len(self.paragraphs)):
            return False
        if letter is not None and letter not in self.LETTERS:
            return False
        if not self._editable(idx):
            return False
        self._push_undo()
        self.speakers[idx] = letter
        self.speaker_origin[idx] = "user"
        if letter is not None and int(letter) > self.visible_speakers:
            self.set_visible(int(letter))
        self._bump()
        return True

    def set_speaker_name(self, letter, name):
        """Rename a slot. No undo step (matches the Tk pane, where each
        keystroke of a rename would otherwise flood the stack), but it
        reschedules the autosave."""
        if letter not in self.LETTERS:
            return False
        self.speaker_names[letter] = name
        self._bump()
        self._schedule_autosave()
        return True

    def set_visible(self, n):
        """Show at least `n` speaker-name fields (clamped)."""
        n = max(self.DEFAULT_VISIBLE, min(int(n), self.MAX_SPEAKERS))
        if n != self.visible_speakers:
            self.visible_speakers = n
            self._bump()
        return self.visible_speakers

    def replace_term(self, variants, replacement):
        """Rewrite every occurrence of any of `variants` as
        `replacement`, across the whole transcript, as a single undo
        step. Returns how many occurrences changed.

        Whole words only, and case-sensitive: capitalisation in a
        transcript is meaningful, and a blind lower-cased replace would
        turn "the Crown" into "the crown" halfway through a sentence.
        """
        replacement = (replacement or "").strip()
        wanted = [v for v in (variants or []) if v and v != replacement]
        if not replacement or not wanted:
            return 0
        # Longest first, so "Mr Cavill" wins over "Cavill".
        pattern = re.compile(
            r"\b(?:%s)\b" % "|".join(
                re.escape(v) for v in sorted(wanted, key=len, reverse=True)))

        hits = sum(len(pattern.findall(seg[2]))
                   for para in self.paragraphs for seg in para)
        if not hits:
            return 0

        self._push_undo()
        for i, para in enumerate(self.paragraphs):
            changed = False
            for k, seg in enumerate(para):
                new_text, n = pattern.subn(replacement, seg[2])
                if n:
                    para[k] = (seg[0], seg[1], new_text)
                    changed = True
            # A spelling the reviewer confirmed is a correction like any
            # other. Without this the repair pass saw "engine" here and
            # put the misspelling back, with no offer raised.
            if changed:
                self.text_origin[i] = "user"
        self._bump()
        return hits

    def commit_edit(self, idx, new_text):
        """Replace paragraph idx's text, collapsing it to one segment
        that keeps the original outer time span. Empty text means
        cancel (no-op); unchanged text records no undo step."""
        if not (0 <= idx < len(self.paragraphs)):
            return False
        new_text = (new_text or "").strip()
        if not new_text:
            return False
        if not self._editable(idx):
            return False
        para = self.paragraphs[idx]
        if not para:
            return False
        if new_text != self.body(idx):
            self._push_undo()
            # Typed over by hand, the paragraph's [indistinct] markers
            # are ordinary text now: nothing to hover, nothing to put
            # back.
            self.marked.pop(self.ids[idx], None)
        start_t = para[0][0]
        end_t = para[-1][1]
        self.paragraphs[idx] = [(start_t, end_t, new_text)]
        self.text_origin[idx] = "user"
        self._bump()
        return True

    def merge_with_previous(self, idx):
        if idx is None or idx <= 0 or idx >= len(self.paragraphs):
            return False
        if not self._editable(idx - 1, idx):
            return False
        self._push_undo()
        # Read before the join: afterwards the counts no longer match.
        joined_words = self.markers_known(idx - 1) + self.markers_known(idx)
        self.paragraphs[idx - 1] = (list(self.paragraphs[idx - 1])
                                    + list(self.paragraphs[idx]))
        # Merging is an editorial act, so the survivor is the user's.
        self.text_origin[idx - 1] = "user"
        self.suggestions.pop(self.ids[idx], None)
        self.marked.pop(self.ids[idx], None)
        if joined_words:
            self.marked[self.ids[idx - 1]] = joined_words
        del self.paragraphs[idx]
        self._drop_parallel(idx)
        self._seal_shift(idx, -1)
        self._bump()
        return True

    def delete_paragraph(self, idx):
        """Remove paragraph idx entirely - for a hallucination or other
        text that isn't real speech. Refused if it is the last remaining
        paragraph (a transcript needs at least one). One undo step."""
        if not (0 <= idx < len(self.paragraphs)):
            return False
        if len(self.paragraphs) <= 1:
            return False
        if not self._editable(idx):
            return False
        self._push_undo()
        self.suggestions.pop(self.ids[idx], None)
        self.marked.pop(self.ids[idx], None)
        del self.paragraphs[idx]
        self._drop_parallel(idx)
        self._seal_shift(idx, -1)
        self._bump()
        return True

    def split(self, idx, offset):
        """Split paragraphs[idx] at character `offset` within body().
        Both halves get real start/end times: preferring the engine's
        word timestamps, falling back to interpolating by character
        position. The new second half keeps the speaker and gets a
        fresh id. Returns the new paragraph's index, or None."""
        if not (0 <= idx < len(self.paragraphs)) or not self._editable(idx):
            return None
        para = self.paragraphs[idx]
        if not para:
            return None
        joined = " ".join(seg[2] for seg in para)
        leading_strip = len(joined) - len(joined.lstrip())
        adjusted_offset = offset + leading_strip
        if adjusted_offset <= 0 or adjusted_offset >= len(joined):
            return None

        seg_starts = []
        running = ""
        for k, (_, _, t) in enumerate(para):
            if k > 0:
                running += " "
            seg_starts.append(len(running))
            running += t

        split_seg = None
        split_within = 0
        for k in range(len(para)):
            seg_start = seg_starts[k]
            seg_end = seg_start + len(para[k][2])
            if adjusted_offset <= seg_start:
                split_seg = k
                split_within = 0
                break
            if adjusted_offset <= seg_end:
                split_seg = k
                split_within = adjusted_offset - seg_start
                break
        if split_seg is None:
            return None

        if split_within == 0:
            first = list(para[:split_seg])
            second = list(para[split_seg:])
        else:
            seg_start_t, seg_end_t, seg_text = para[split_seg]
            text_before = seg_text[:split_within].rstrip()
            text_after = seg_text[split_within:].lstrip()
            split_t = self._time_at_body_offset(para, joined,
                                                adjusted_offset)
            if split_t is None and seg_text:
                split_t = (seg_start_t
                           + (seg_end_t - seg_start_t)
                           * (split_within / len(seg_text)))
            if split_t is None:
                split_t = seg_start_t
            split_t = max(seg_start_t, min(float(split_t), seg_end_t))
            first = list(para[:split_seg])
            if text_before:
                first.append((seg_start_t, split_t, text_before))
            second = []
            if text_after:
                second.append((split_t, seg_end_t, text_after))
            second.extend(para[split_seg + 1:])

        if not first or not second:
            return None

        self._push_undo()
        self.paragraphs[idx] = first
        self.paragraphs.insert(idx + 1, second)
        # Splitting is an editorial act: both halves are now the
        # user's, and the speaker carries across to the new half.
        self._insert_parallel(idx + 1,
                              speakers=self.speakers[idx],
                              speaker_origin=self.speaker_origin[idx],
                              text_origin="user")
        self.text_origin[idx] = "user"
        self.suggestions.pop(self.ids[idx], None)
        words = self.marked.pop(self.ids[idx], None)
        if words:
            n = self.body(idx).count(self.INDISTINCT)
            self.marked[self.ids[idx]] = words[:n]
            self.marked[self.ids[idx + 1]] = words[n:]
        self._seal_shift(idx, +1)
        self._bump()
        return idx + 1

    # ----- Growing while the engine is still running ------------------

    def _editable(self, *indices):
        """Whether a person may change these paragraphs. The engine's
        provisional tail is still being re-grouped as segments arrive,
        so an edit there would be silently thrown away."""
        return all(self.is_sealed(i) for i in indices)

    def is_sealed(self, idx):
        """Whether paragraph idx is final. The provisional tail belongs
        to the engine until the run moves past it."""
        return 0 <= idx < self.sealed_count

    def extend(self, sealed, tail=None):
        """Replace the provisional tail with `sealed` (now final) plus
        an optional new provisional paragraph.

        This is the only way the engine adds to a transcript somebody
        may be reading. It never reaches behind sealed_count, so an
        edit already made cannot be disturbed by a segment arriving
        later. No undo step: growing is not something to undo, and it
        would otherwise fill the stack during a long run."""
        lo = self.sealed_count
        hi = len(self.paragraphs) - 1
        fresh = [list(p) for p in sealed]
        if tail:
            fresh.append(list(tail))
        for old_id in self.ids[lo:]:
            self.suggestions.pop(old_id, None)
        self.paragraphs[lo:] = fresh
        if hi >= lo:
            self._splice_parallel(lo, hi, len(fresh))
        else:
            self.ids[lo:] = [self._new_id() for _ in fresh]
            for name, default in self._PARALLEL_DEFAULTS.items():
                getattr(self, name)[lo:] = [default] * len(fresh)
        self.sealed_count = lo + len(sealed)
        self._grown += len(sealed)
        # Growth is not an edit: it cannot touch anything a person is
        # working on, so it must not invalidate an edit in flight.
        self._bump(edit=False)
        self._schedule_autosave(growth=True)
        return self.rev

    def seal_all(self):
        """Mark everything final - the run has finished or stopped."""
        # The tail becoming final is the engine's last contribution, and
        # counts as growth like any other for the benefit of snapshots
        # taken before it.
        self._grown += len(self.paragraphs) - self.sealed_count
        self.sealed_count = len(self.paragraphs)
        self._bump()
        return self.rev

    # ----- Writes that come from the engine, not from a person --------

    def apply_engine_speakers(self, letters):
        """Fill in detected speaker slots, skipping every paragraph
        whose speaker a person has already set. Returns how many were
        filled.

        Positional: letter `i` goes to paragraph `i`. Only safe when
        the transcript has not moved since detection ran - see
        apply_engine_speakers_by_time for when it has."""
        if not letters:
            return 0
        return self._fill_speakers(
            (idx, letter) for idx, letter in enumerate(letters))

    def apply_engine_speakers_by_time(self, spans):
        """Fill in detected speaker slots by matching each paragraph to
        the stretch of recording it covers, rather than by position.

        Detection hands back one letter per paragraph as the engine
        produced them. The reviewer can now delete, split and merge
        paragraphs while the run is still going, so by the time those
        letters arrive the transcript may be a different length - and a
        positional list applied to a transcript that has moved
        underneath it attributes answers to the questioner and questions
        to the witness. That is worse than garbled text: it is fluent,
        plausible, and wrong about who spoke.

        Times survive every edit a person can make; indices do not.
        `spans` is [(start, end, letter)] from the engine's own
        paragraphs, and each paragraph here takes the letter of the span
        it shares the most recording with."""
        spans = [(float(a), float(b), L) for a, b, L in (spans or [])
                 if L is not None and float(b) > float(a)]
        if not spans:
            return 0

        def letter_for(idx):
            para = self.paragraphs[idx]
            if not para:
                return None
            start, end = float(para[0][0]), float(para[-1][1])
            best, best_overlap = None, 0.0
            for a, b, letter in spans:
                overlap = min(end, b) - max(start, a)
                if overlap > best_overlap:
                    best, best_overlap = letter, overlap
            return best

        return self._fill_speakers(
            (idx, letter_for(idx)) for idx in range(len(self.paragraphs)))

    def _fill_speakers(self, pairs):
        """Apply (index, letter) pairs from an automatic writer, leaving
        anything a person set alone. Returns how many changed."""
        filled = 0
        for idx, letter in pairs:
            if not (0 <= idx < len(self.paragraphs)) or letter is None:
                continue
            if self.speaker_origin[idx] == "user":
                continue
            if self.speakers[idx] == letter:
                continue
            if filled == 0:
                self._push_undo()
            self.speakers[idx] = letter
            self.speaker_origin[idx] = "engine"
            if int(letter) > self.visible_speakers:
                self.set_visible(int(letter))
            filled += 1
        if filled:
            self._bump()
        return filled

    def offer_engine_run(self, lo, hi, new_paragraphs, *, source):
        """The engine has re-read paragraphs [lo..hi] and produced
        something better by its own test.

        If nobody has edited that stretch it is simply applied. If
        somebody has, their text stands and the engine's version is
        kept as an offer against the first paragraph of the span - the
        reviewer can look at it and take it or leave it. Overwriting a
        correction someone made deliberately, even with a better
        automatic reading, is not a trade a transcript can make on its
        own.

        Returns "applied", "offered", or "stale"."""
        n = len(self.paragraphs)
        if not new_paragraphs or not (0 <= lo <= hi < n):
            return "stale"
        # The engine's provisional tail is not the repair pass's to
        # rewrite - the streaming fold is still re-grouping it, and
        # extend() would discard the result on the next segment anyway.
        if not self._editable(lo, hi):
            return "stale"
        # A speaker somebody assigned counts as a correction just as
        # much as text they retyped. It is the commonest thing a
        # reviewer does during a run, and replace_runs would reset it
        # to None without the offer ever being raised.
        touched = any(self.text_origin[i] == "user"
                      or self.speaker_origin[i] == "user"
                      for i in range(lo, hi + 1))
        if not touched:
            self.replace_runs([(lo, hi, new_paragraphs)])
            return "applied"
        self.suggestions[self.ids[lo]] = {
            "source": source,
            "end_id": self.ids[hi],
            "paragraphs": [[tuple(seg) for seg in para]
                           for para in new_paragraphs],
            "text": " ".join(seg[2] for para in new_paragraphs
                             for seg in para).strip(),
        }
        self._bump()
        self._schedule_autosave()
        return "offered"

    def suggestion_for(self, idx):
        """The engine's alternative for paragraph idx, or None."""
        if not (0 <= idx < len(self.paragraphs)):
            return None
        return self.suggestions.get(self.ids[idx])

    def _suggestion_span(self, idx):
        """(lo, hi) the stored suggestion at idx still covers, or None
        if editing since has made it meaningless."""
        offer = self.suggestion_for(idx)
        if offer is None:
            return None
        try:
            hi = self.ids.index(offer["end_id"])
        except ValueError:
            return None             # the far end has been merged away
        if hi < idx:
            return None
        return idx, hi

    def accept_suggestion(self, idx):
        """Take the engine's alternative for the span starting at idx."""
        span = self._suggestion_span(idx)
        offer = self.suggestion_for(idx)
        if span is None:
            if offer is not None:
                self.suggestions.pop(self.ids[idx], None)
                self._bump()
            return False
        lo, hi = span
        if not self._editable(lo, hi):
            return False
        self.replace_runs([(lo, hi, offer["paragraphs"])])
        return True

    def dismiss_suggestion(self, idx):
        """Discard the engine's alternative and keep what is there."""
        if not (0 <= idx < len(self.paragraphs)):
            return False
        if self.suggestions.pop(self.ids[idx], None) is None:
            return False
        self._bump()
        self._schedule_autosave()
        return True

    def set_ts_mark(self, idx, value):
        """Set paragraph idx's timestamp state: None (show the computed
        time), "hidden", or a number of seconds to show instead."""
        if not (0 <= idx < len(self.paragraphs)):
            return False
        if value is not None and value != "hidden":
            try:
                value = max(0.0, float(value))
            except (TypeError, ValueError):
                return False
        if value == self.ts_marks[idx]:
            return True
        self._push_undo()
        self.ts_marks[idx] = value
        self._bump()
        return True

    def replace_runs(self, runs):
        """Atomically replace several disjoint paragraph runs with fresh
        paragraphs - the re-transcribe splice. `runs` is a list of
        (lo, hi, new_paragraphs); they must be non-overlapping and in
        ascending order. Applied right-to-left so earlier indices don't
        shift. Replacements arrive unlabelled with clean timestamp
        state. One undo step covers the whole operation."""
        runs = [r for r in runs if r[2]]
        if not runs:
            return False
        n = len(self.paragraphs)
        for lo, hi, _ in runs:
            if not (0 <= lo <= hi < n):
                return False
        self._push_undo()
        for lo, hi, new_paragraphs in sorted(runs, reverse=True):
            for old_id in self.ids[lo:hi + 1]:
                self.suggestions.pop(old_id, None)
            k = len(new_paragraphs)
            self.paragraphs[lo:hi + 1] = [list(p) for p in new_paragraphs]
            self._splice_parallel(lo, hi, k)
            self._seal_shift(lo, k - (hi - lo + 1))
        self._bump()
        return True

    def replace_all(self, term, replacement, match_case=False):
        """Replace every occurrence across the document. One undo step;
        returns the number of replacements (0 = nothing recorded)."""
        if not term:
            return 0
        flags = 0 if match_case else re.IGNORECASE
        pattern = re.compile(re.escape(term), flags)
        total = sum(pattern.subn(lambda _m: replacement, seg[2])[1]
                    for para in self.paragraphs for seg in para)
        if total == 0:
            return 0
        self._push_undo()
        for i, para in enumerate(self.paragraphs):
            changed = False
            for k, seg in enumerate(para):
                new_text, n = pattern.subn(lambda _m: replacement, seg[2])
                if n:
                    para[k] = (seg[0], seg[1], new_text)
                    changed = True
            if changed:
                self.text_origin[i] = "user"
        self._bump()
        return total


def _resolve_word_timestamps(mode, engine_key):
    """Turn the word_timestamps setting ("auto"/"on"/"off") into a bool
    for the engine that will actually run. "auto" is on everywhere. It
    used to skip mlx-whisper, on the belief that word alignment tripled
    the run there; the cost is far smaller than that, mlx stays far
    faster than faster-whisper, and paragraphs, playback and review all
    need the timings.
    `engine_key` stays in the signature for callers and any future
    engine where the cost really is prohibitive."""
    if mode == "off":
        return False
    return True                     # "on" and "auto"


def compose_initial_prompt(settings, terms=None, source_path=None):
    """The engine's initial_prompt: whatever the user typed, plus the
    terms confirmed in this recording's folder, when priming is on.

    Priming is a real trade. Naming "Cavill" makes the engine far more
    likely to spell it right where it is said - and somewhat more likely
    to hear it where it was not. That is a poor bargain for a transcript
    relied on as a record, so it is off unless asked for, and the terms
    come only from corrections the reviewer has already confirmed.

    Terms are taken from the file's own folder and nowhere else. Names
    belonging to one set of recordings have no business being suggested
    to the engine while it transcribes somebody else's.
    """
    typed = (settings.get("prompt") or "").strip()
    if not settings.get("prime_with_terms"):
        return typed
    known = list(terms if terms is not None else _terms_load(source_path))
    if not known:
        return typed
    # A plain comma list is what Whisper-family models take as context.
    listed = ", ".join(known[:60])
    return f"{typed} {listed}".strip() if typed else listed


def build_worker_params(settings, in_path, out_path, *,
                        review_before_save):
    """Assemble the transcribe_worker params dict from a validated
    settings dict for one (in_path -> out_path) job - the module-level
    twin of WhisperGUI._build_params. Raises _EngineNotAvailable when
    no Whisper engine is installed."""
    if not available_engines():
        raise _EngineNotAvailable(
            "No Whisper engine is installed in this Python.\n\n"
            "Install at least one:\n"
            "  pip install openai-whisper\n"
            "  pip install faster-whisper\n"
            "  pip install mlx-whisper   (Apple Silicon only)\n")

    lang_code = next(
        (c for n, c in LANGUAGES if n == settings["language"]), "en")

    extra_formats = [fmt for fmt in ("json", "srt", "vtt", "tsv")
                     if settings.get(f"extra_{fmt}")]

    # Title and prompt are separate: the document title is never fed to
    # the engine, and the optional prompt (a vocabulary hint) is fed as
    # initial_prompt. Prompting is opt-in because it can backfire - it can
    # bleed into the transcript or trigger hallucinations on unclear audio.
    # Confirmed terms join the typed hint only when priming is on, and
    # only those confirmed alongside this recording.
    prompt = compose_initial_prompt(settings, source_path=in_path)
    doc_title = (settings.get("title") or "").strip()

    engine_key = resolve_engine_key(settings["engine"])

    return dict(
        input=in_path,
        output=out_path,
        engine=engine_key,
        model=settings["model"],
        language=lang_code,
        task=settings["task"],
        temperature=settings["temperature"],
        beam_size=settings["beam_size"],
        best_of=settings["best_of"],
        compression_ratio_threshold=settings[
            "compression_ratio_threshold"],
        logprob_threshold=settings["logprob_threshold"],
        no_speech_threshold=settings["no_speech_threshold"],
        condition_on_previous_text=settings["condition_on_previous_text"],
        # Word-level timings sharpen paragraph gaps, playback spans and
        # feed confidence shading; "auto" records them on every engine.
        word_timestamps=_resolve_word_timestamps(
            settings.get("word_timestamps", "auto"), engine_key),
        initial_prompt=prompt or None,
        # With no title, name the document after the source file. (The
        # filename is never fed to the engine - recorder names like
        # REC_0042 would only mislead it.)
        title=doc_title or Path(in_path).name,
        gap=settings["gap"],
        voice_breaks=bool(settings.get("voice_breaks", True)),
        extra_formats=extra_formats,
        output_format=settings["output_format"],
        show_timestamp=settings["show_timestamp"],
        colour_speakers=bool(settings.get("colour_speakers")),
        doc_template=settings.get("doc_template", "plain"),
        audio_duration=get_audio_duration(in_path),
        review_before_save=review_before_save,
        diarize=bool(settings.get("diarize")),
        num_speakers=int(settings.get("num_speakers") or 0),
        diarize_model=(settings.get("diarize_model")
                       or DIARIZE_DEFAULT_VOICE_MODEL),
        diarize_threshold=float(settings.get("diarize_threshold")
                                or _DIARIZE_CLUSTER_THRESHOLD),
    )

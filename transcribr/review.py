"""One open review session: a transcript model plus its document."""

from pathlib import Path
import os
import threading
import time

from .errors import ApiFail, TranscriptParseError
from .platformutil import (
    _autosave_clear, _autosave_save, _guess_audio_for_transcript, _log,
    _next_revision_path, _recent_add, _terms_add
)
from .paragraphs import paragraphify
from .writers import (
    _reviewed_result, _write_extra_formats, write_paragraphs_to_file
)
from .readers import read_paragraphs_from_file
from .worker import detect_hallucinations
from .transcript import TranscriptModel
from .settings import current_settings


def rerun_model(run_model, settings):
    """The model a re-transcription should default to: one from the
    other family to the one that made the transcript. A passage one
    engine got wrong - a loop, an invented sentence - is a passage it
    tends to get wrong the same way twice, so asking the same model
    again is the least useful retry."""
    from .engines import PARAKEET_MODEL, _canonical_model
    original = _canonical_model(run_model or settings.get("model") or "")
    english = settings.get("language") in ("English", "en")
    if original == PARAKEET_MODEL:
        return "large-v3-turbo"
    if english:
        return PARAKEET_MODEL
    return "large-v3-turbo" if original == "large-v3" else "large-v3"


def open_transcript_info(path):
    """Parse a saved transcript into the review-session info dict - the
    module-level twin of WhisperGUI._load_transcript's parsing half.
    Raises ApiFail for parse errors and the >9-speakers refusal."""
    try:
        parsed = read_paragraphs_from_file(path)
    except TranscriptParseError as e:
        raise ApiFail(400, "parse_error", str(e))
    except Exception as e:
        raise ApiFail(400, "parse_error",
                      f"Unexpected error reading {path}:\n"
                      f"{type(e).__name__}: {e}")

    max_speakers = TranscriptModel.MAX_SPEAKERS
    unique_names = []
    for name in parsed["speakers"]:
        if name and name not in unique_names:
            unique_names.append(name)
    if len(unique_names) > max_speakers:
        raise ApiFail(
            422, "too_many_speakers",
            f"This transcript contains {len(unique_names)} distinct "
            f"speakers. The review pane only supports up to "
            f"{max_speakers}. Open the file in Word and consolidate the "
            "speaker labels before loading it back into Transcribr.",
            count=len(unique_names), max=max_speakers)

    name_to_letter = {}
    preset_speaker_names = {}
    for i, name in enumerate(unique_names):
        letter = str(i + 1)
        name_to_letter[name] = letter
        preset_speaker_names[letter] = name
    preset_speakers = [name_to_letter.get(name) if name else None
                       for name in parsed["speakers"]]

    return {
        "paragraphs": parsed["paragraphs"],
        "out_path": path,
        "show_timestamp": parsed["show_timestamp"],
        "title": parsed.get("title"),
        "output_format": ("docx" if path.lower().endswith(".docx")
                          else "txt"),
        "used_partial": False,
        "result": None,
        "extra_formats": [],
        "preset_speakers": preset_speakers,
        "preset_speaker_names": preset_speaker_names,
        # A transcript with no stamps anywhere carries no timing at
        # all, so per-paragraph playback is not possible from it.
        "has_timings": bool(parsed["show_timestamp"]),
        "preset_ts_marks": (
            ["hidden" if h else None for h in parsed.get("ts_hidden") or []]
            if parsed.get("show_timestamp") else []),
        "verified_by": parsed.get("verified_by"),
        # What each [indistinct] replaced, kept in a .docx.
        "preset_marked": parsed.get("indistinct") or None,
        # A saved Q&A layout reopens in Q&A mode, with its askers.
        "layout": parsed.get("layout") or "plain",
        "askers": ([name_to_letter[n] for n in parsed["askers_names"]
                    if n in name_to_letter]
                   if parsed.get("layout") == "qa"
                   and parsed.get("askers_names") else None),
        "loaded": True,
        # Prefer the audio location embedded in the .docx metadata
        # (when it still exists on disk) - absolute first, then the
        # transcript-relative one, which keeps working after the whole
        # folder has been moved; fall back to the filename-sibling
        # guess.
        "audio_path": next(
            (p for p in (parsed.get("audio_path"),
                         parsed.get("audio_path_rel"))
             if p and Path(p).exists()),
            _guess_audio_for_transcript(path)),
    }


def autosave_restore_info(data):
    """Rebuild the review info dict from an autosave.json payload - the
    twin of _maybe_offer_autosave_restore's restore half."""
    paragraphs = [[tuple(seg) for seg in para]
                  for para in data["paragraphs"]]
    return {
        "paragraphs": paragraphs,
        "out_path": data.get("out_path", ""),
        "show_timestamp": data.get("show_timestamp", True),
        "colour_speakers": bool(data.get("colour_speakers")),
        "preset_text_origin": data.get("text_origin") or None,
        "preset_speaker_origin": data.get("speaker_origin") or None,
        "preset_suggestions": {int(k): v for k, v in
                               (data.get("suggestions") or {}).items()},
        "title": data.get("title"),
        "output_format": data.get("output_format", "txt"),
        "used_partial": False,
        "result": None,
        "extra_formats": [],
        "loaded": bool(data.get("loaded")),
        "diarized": bool(data.get("diarized")),
        "verified_by": data.get("verified_by"),
        "audio_path": data.get("audio_path"),
        "preset_speakers": data.get("speakers") or [],
        "preset_speaker_names": data.get("speaker_names") or {},
        "preset_ts_marks": data.get("ts_marks") or [],
    }


class ReviewSession:
    """One open review: a TranscriptModel plus document metadata and
    the save/close orchestration - the web twin of WhisperGUI's review
    host code. Mutations must present the client's last-seen model rev;
    a mismatch raises ApiFail 409 so a stale window can't clobber the
    session."""

    def __init__(self, info, broker, *, log=None):
        self.broker = broker
        self.info = info
        self.out_path = str(info.get("out_path", ""))
        self.show_timestamp = bool(info.get("show_timestamp", True))
        # Seeded from Settings for a fresh run and carried in the
        # autosave for a restored one; the review pane's own save
        # options can override it per document.
        self.colour_speakers = bool(
            info.get("colour_speakers",
                     current_settings().get("colour_speakers", False)))
        self.title = info.get("title")
        # The model that produced this transcript, when this session
        # saw the run (None for one opened from a file).
        self.run_model = info.get("model")
        # "plain" or "qa": how the pane shows the transcript and how
        # Save writes it. `askers` names the speaker slots treated as
        # asking the questions; None means suggest them from the
        # wording each time.
        self.layout = info.get("layout") or "plain"
        self.askers = info.get("askers")
        self.output_format = info.get("output_format", "txt")
        self.loaded = bool(info.get("loaded"))
        # True while the engine is still adding to this transcript.
        self.live = False
        self.audio_path = info.get("audio_path")
        self.result = info.get("result")
        self.extra_formats = info.get("extra_formats") or []
        self.diarized = bool(info.get("diarized"))
        self.verified_by = ((info.get("verified_by") or "").strip()
                            or None)
        self._safety_path = None    # set by from_fresh after its pre-save
        self._log_line = log or (lambda text: None)
        self.lock = threading.RLock()
        self.closed = False
        self.audio_status_fn = None   # set by the backend (AudioPrep)
        # Paragraph ids currently being re-transcribed. Those paragraphs
        # are locked (greyed out, un-editable) while the engine runs;
        # editing elsewhere stays free, and the result is spliced back
        # by identity so those edits survive.
        self._locked_ids = frozenset()
        preset_speakers = info.get("preset_speakers") or None
        self.model = TranscriptModel(
            info["paragraphs"],
            speakers=preset_speakers,
            speaker_names=info.get("preset_speaker_names") or None,
            word_conf=info.get("word_conf"),
            ts_marks=info.get("preset_ts_marks") or None,
            on_autosave=self._write_autosave,
            has_timings=info.get("has_timings", True),
            text_origin=info.get("preset_text_origin") or None,
            speaker_origin=info.get("preset_speaker_origin") or None,
            suggestions=info.get("preset_suggestions") or None,
            marked=info.get("preset_marked") or None,
        )
        # Reveal enough name fields to cover preset slots (parity with
        # _enter_review_mode's set_visible_speakers call).
        names = info.get("preset_speaker_names") or {}
        slots = ([int(L) for L in names.keys()]
                 + [int(L) for L in (preset_speakers or []) if L])
        if slots:
            self.model.set_visible(max(slots))

    @classmethod
    def from_fresh(cls, info, broker, *, log=None):
        """Session for a fresh transcription: crash-safety pre-save of
        the un-labelled transcript before review opens (port of
        _enter_review_mode lines 5992-6010)."""
        session = cls(info, broker, log=log)
        if info.get("result") is not None:
            try:
                # Defence in depth (the run validators already refuse
                # this): never let the safety copy touch the recording.
                audio = info.get("audio_path")
                if audio and os.path.normcase(
                        str(Path(audio).resolve())) == os.path.normcase(
                        str(Path(info["out_path"]).resolve())):
                    raise RuntimeError(
                        "safety copy would overwrite the source audio")
                write_paragraphs_to_file(
                    info["paragraphs"], Path(info["out_path"]),
                    show_timestamp=info.get("show_timestamp", True),
                    title=info.get("title"),
                    output_format=info["output_format"],
                    speakers=None,
                )
                session._safety_path = str(info["out_path"])
                session._log_line(
                    f"Safety copy saved (no labels yet): "
                    f"{info['out_path']}\n")
            except Exception as e:
                _log(f"Safety save before review failed: {e}")
                session._log_line(
                    f"Warning: could not save safety copy "
                    f"({type(e).__name__}: {e}). Continuing to review.\n")
        return session

    # ----- Payload -------------------------------------------------------

    @classmethod
    def open_live(cls, info, broker, *, log=None):
        """A session for a run that has only just started.

        It opens with nothing in it and grows as the engine reports
        paragraphs, so the reviewer can read and correct the beginning
        of a recording while the end of it is still being transcribed."""
        session = cls({**info, "paragraphs": []}, broker, log=log)
        session.live = True
        session.model.sealed_count = 0
        return session

    def grow(self, sealed, tail):
        """Take newly finished paragraphs from the engine. Returns the
        payload describing what changed, or None once closed."""
        with self.lock:
            if self.closed:
                return None
            m = self.model
            first = m.sealed_count
            m.extend(sealed, tail)
            return {
                "rev": m.rev,
                "from": first,
                "sealed_count": m.sealed_count,
                "total": len(m.paragraphs),
                "paragraphs": self._paragraph_payloads(first),
                # The Q&A marks can change all the way down as the
                # transcript grows (a new asker renumbers nothing above,
                # but the wording model re-reads the whole exchange).
                **({"qa": self._qa_payload()["qa"]}
                   if self.layout == "qa" else {}),
            }

    def finish_live(self, info):
        """The run is over: seal what is left, take the engine's
        speaker labels where nobody has set one, and keep the word
        confidences the pane shades from."""
        with self.lock:
            if self.closed:
                return None
            m = self.model
            m.seal_all()
            self.live = False
            self.result = info.get("result")
            self.extra_formats = info.get("extra_formats") or []
            if info.get("word_conf"):
                m.word_conf = info["word_conf"]
            letters = info.get("preset_speakers")
            if letters:
                # Detection labels the engine's paragraphs, one letter
                # each, and by now the reviewer may have deleted, split
                # or merged some of them - so the list and the document
                # need no longer be the same length, let alone line up.
                # Applied by position, one deletion early in a run
                # shifted every label after it and handed the witness's
                # answers to the person asking the questions. Match on
                # the recording instead, which no edit moves.
                engine = info.get("paragraphs") or []
                if len(engine) == len(letters):
                    filled = m.apply_engine_speakers_by_time(
                        [(p[0][0], p[-1][1], letter)
                         for p, letter in zip(engine, letters) if p])
                else:
                    filled = m.apply_engine_speakers(letters)
                self.diarized = bool(filled) or self.diarized
        return self.payload()

    def _paragraph_payloads(self, start=0):
        """The per-paragraph payloads from `start` onward.

        Only the slice asked for is built. Growth calls this once per
        engine segment, and building the whole document each time made
        the cost of a run quadratic in the length of the run."""
        m = self.model
        lo = max(0, start)
        conf = m.confidence_spans()
        confidences = (
            [[p for _w, p in bucket]
             for bucket in m._bucket_words_by_paragraph()]
            if m.word_conf else None)
        # detect_hallucinations compares each paragraph with the one
        # before it, so the window starts one early; that extra result
        # is outside the slice and simply not asked for.
        ctx = max(0, lo - 1)
        suspect = {ctx + i for i in detect_hallucinations(
            m.paragraphs[ctx:],
            confidences=(None if confidences is None
                         else confidences[ctx:]))}
        # The offers can be switched off; the markers already accepted
        # stay, with the words they replaced, either way.
        unsure = (m.indistinct_suggestions()
                  if current_settings().get("offer_indistinct", True)
                  else [[] for _ in m.paragraphs])
        return [self._one_paragraph(i, conf, suspect, unsure)
                for i in range(lo, len(m.paragraphs))]

    def _one_paragraph(self, i, conf, suspect, unsure):
        m = self.model
        para = m.paragraphs[i]
        span = m.playback_span(i)
        if span is None:
            play = None
        else:
            start, dur = span
            play = {"start": start,
                    "end": None if dur is None else start + dur}
        offer = m.suggestions.get(m.ids[i])
        return {
            "id": m.ids[i],
            "start": para[0][0] if para else 0.0,
            "end": para[-1][1] if para else 0.0,
            "body": m.body(i),
            "speaker": m.speakers[i],
            "ts": m.ts_marks[i],
            "suspect": i in suspect,
            "locked": m.ids[i] in self._locked_ids,
            "play": play,
            "conf": [list(s) for s in conf[i]],
            # Passages to offer as [indistinct], for the reviewer to
            # accept or keep - never applied unasked.
            "unsure": [list(s) for s in unsure[i]],
            # Each accepted [indistinct]: [start, end, words it replaced
            # or null], so hovering it can show them and put them back.
            "markers": [list(s) for s in m.marker_spans(i)],
            # Who last wrote it, whether the engine may still reshape
            # it, and what the engine would rather it said. The pane
            # needs all three to show a correction as protected and an
            # alternative as an offer rather than a change.
            "mine": m.text_origin[i] == "user",
            "speaker_mine": m.speaker_origin[i] == "user",
            "sealed": m.is_sealed(i),
            "offer": (None if offer is None
                      else {"text": offer["text"],
                            "source": offer["source"]}),
        }

    def _audio_payload(self):
        """Audio status for the review pane. A transcript parsed back
        from a file with no timestamps has no per-paragraph times, so
        however playable the recording is, nothing can be lined up
        against it - say so rather than letting every paragraph play
        from the top of the recording."""
        if not self.model.has_timings:
            return {
                "state": "unavailable",
                "error": ("this transcript was saved without "
                          "timestamps, so Transcribr can't tell where "
                          "each paragraph falls in the recording. "
                          "Re-save it with timestamps shown to restore "
                          "paragraph playback"),
            }
        if self.audio_status_fn is None:
            return {"state": "unavailable"}
        return self.audio_status_fn()

    def payload(self):
        with self.lock:
            m = self.model
            paragraphs = self._paragraph_payloads()
            labelled, total = m.label_counts()
            return {
                "rev": m.rev,
                "live": self.live,
                "sealed_count": m.sealed_count,
                "out_path": self.out_path,
                "output_format": self.output_format,
                "show_timestamp": self.show_timestamp,
                "colour_speakers": self.colour_speakers,
                "title": self.title,
                "run_model": self.run_model,
                **self._qa_payload(),
                "retrans_default": rerun_model(
                    self.run_model, current_settings()),
                "loaded": self.loaded,
                "audio": self._audio_payload(),
                "speaker_names": dict(m.speaker_names),
                "visible_speakers": m.visible_speakers,
                "labelled": labelled,
                "total": total,
                "can_undo": m.can_undo(),
                "can_redo": m.can_redo(),
                "has_word_conf": bool(m.word_conf),
                "diarized": self.diarized,
                "verified_by": self.verified_by,
                "paragraphs": paragraphs,
            }

    def _qa(self):
        """The Q&A layout of the current transcript (lock held)."""
        from .qa import qa_layout
        m = self.model
        names = {s: (m.speaker_names.get(s) or "").strip()
                 or f"Speaker {s}" for s in m.LETTERS}
        return qa_layout(m.paragraphs, list(m.speakers),
                         askers=self.askers, names=names)

    def _qa_payload(self):
        if self.layout != "qa":
            return {"layout": "plain", "qa": None}
        lay = self._qa()
        return {"layout": "qa",
                "qa": {"rows": lay["rows"], "askers": lay["askers"],
                       "auto": self.askers is None,
                       "method": lay["method"], "note": lay["note"]}}

    def _slim(self, **extra):
        labelled, total = self.model.label_counts()
        # A speaker change moves the Q&A marks, so in that mode the
        # slim reply carries them too.
        qa = ({"qa": self._qa_payload()["qa"]}
              if self.layout == "qa" else {})
        return {"rev": self.model.rev, "labelled": labelled,
                "total": total,
                "visible_speakers": self.model.visible_speakers,
                **qa, **extra}

    # ----- Mutations ------------------------------------------------------

    def _check_rev(self, rev):
        """Refuse a mutation composed against a document that has since
        changed under the client.

        The engine extending the transcript does not count. It bumps the
        model's rev - clients do need to refetch to see the new
        paragraphs - but it only ever appends past sealed_count, and a
        person can only ever edit before it, so the two cannot collide.
        Comparing against `rev` itself meant every segment invalidated
        every edit in flight, and on a fast engine roughly two in five
        corrections made during a run were thrown away."""
        if rev < self.model.edit_rev:
            raise ApiFail(409, "stale_rev",
                          "The document changed under you - refetch.",
                          rev=self.model.rev)

    def _guard_not_locked(self, action, body):
        """Refuse edits that touch a paragraph currently being
        re-transcribed. Structural edits elsewhere are fine - the
        splice re-finds its target by identity afterwards."""
        if not self._locked_ids:
            return
        m = self.model
        # Whole-document actions cannot say which paragraph they mean,
        # and every one of them can reach into the locked span - a term
        # correction applied there is spliced over the moment the engine
        # comes back, and an accepted offer or an undo rewrites it
        # outright. They are refused for the few seconds it takes.
        if action in ("replace-all", "term", "undo", "redo",
                      "accept-offer", "dismiss-offer"):
            raise ApiFail(
                409, "locked",
                "A section is being re-transcribed. This applies to the "
                "whole transcript, so it waits until that finishes - a "
                "moment away.")
        idx = None
        if action in ("speaker", "edit", "split", "timestamp", "merge",
                      "delete", "indistinct", "indistinct-keep",
                      "indistinct-restore"):
            idx = int(body.get("index", -1))
        if idx is None:
            return
        touched = []
        if 0 <= idx < len(m.ids):
            touched.append(m.ids[idx])
        # A merge also folds in the previous paragraph.
        if action == "merge" and 0 <= idx - 1 < len(m.ids):
            touched.append(m.ids[idx - 1])
        if any(pid in self._locked_ids for pid in touched):
            raise ApiFail(
                409, "locked",
                "That paragraph is being re-transcribed. You can edit "
                "the rest of the transcript meanwhile; this section "
                "updates when it finishes.")

    def mutate(self, rev, action, body):
        """Apply one named mutation; returns a slim delta for hot ops
        and the full payload for structural ones."""
        with self.lock:
            self._check_rev(rev)
            self._guard_not_locked(action, body)
            m = self.model
            if action == "speaker":
                idx = int(body.get("index", -1))
                letter = body.get("slot")
                if not m.set_speaker(idx, letter):
                    raise ApiFail(400, "bad_request", "Bad index or slot.")
                result = self._slim(index=idx, speaker=letter)
            elif action == "speaker-name":
                if not m.set_speaker_name(str(body.get("slot")),
                                          str(body.get("name", ""))):
                    raise ApiFail(400, "bad_request", "Bad slot.")
                result = self._slim(speaker_names=dict(m.speaker_names))
            elif action == "visible-speakers":
                m.set_visible(int(body.get("n", m.DEFAULT_VISIBLE)))
                result = self._slim()
            elif action == "edit":
                if not m.commit_edit(int(body.get("index", -1)),
                                     str(body.get("text", ""))):
                    raise ApiFail(409, "not_editable",
                                  "That paragraph can't be edited yet - "
                                  "the engine is still working on it.")
                result = self.payload()
            elif action == "split":
                new_idx = m.split(int(body.get("index", -1)),
                                  int(body.get("offset", -1)))
                if new_idx is None:
                    raise ApiFail(409, "not_editable",
                                  "That paragraph can't be split yet - "
                                  "the engine is still working on it.")
                result = self.payload()
                result["new_index"] = new_idx
            elif action == "merge":
                if not m.merge_with_previous(int(body.get("index", -1))):
                    raise ApiFail(400, "bad_request",
                                  "Can't merge the first paragraph.")
                result = self.payload()
            elif action == "accept-offer":
                idx = int(body.get("index", -1))
                if not m.accept_suggestion(idx):
                    raise ApiFail(409, "stale_offer",
                                  "That passage has changed since the "
                                  "engine re-read it.")
                result = self.payload()
            elif action == "dismiss-offer":
                if not m.dismiss_suggestion(int(body.get("index", -1))):
                    raise ApiFail(400, "bad_request",
                                  "There is nothing offered there.")
                result = self.payload()
            elif action == "delete":
                if not m.delete_paragraph(int(body.get("index", -1))):
                    raise ApiFail(400, "bad_request",
                                  "Can't delete the last paragraph.")
                result = self.payload()
            elif action == "timestamp":
                if not m.set_ts_mark(int(body.get("index", -1)),
                                     body.get("value")):
                    raise ApiFail(400, "bad_request",
                                  "Bad index or timestamp value.")
                result = self.payload()
            elif action == "replace-all":
                count = m.replace_all(str(body.get("find", "")),
                                      str(body.get("replace", "")),
                                      bool(body.get("match_case")))
                result = self.payload()
                result["count"] = count
            elif action == "term":
                # Distinct from replace-all: whole words only, several
                # spellings at once, and one undo step for the lot.
                to = str(body.get("to", ""))
                count = m.replace_term(body.get("variants") or [], to)
                if count:
                    # The reviewer has just told us this spelling is
                    # right; that is the only trustworthy source of
                    # vocabulary this app has. Filed against this
                    # recording's folder, so it helps the rest of the
                    # same set and reaches nothing else.
                    _terms_add(self.audio_path or self.out_path, to)
                result = self.payload()
                result["count"] = count
            elif action == "indistinct-restore":
                if not m.restore_indistinct(int(body.get("index", -1)),
                                            int(body.get("ordinal", -1)),
                                            expect=body.get("text")):
                    raise ApiFail(409, "not_editable",
                                  "Those words can't be put back - the "
                                  "paragraph has changed.")
                result = self.payload()
            elif action in ("indistinct", "indistinct-keep"):
                idx = int(body.get("index", -1))
                a, b = int(body.get("start", -1)), int(body.get("end", -1))
                done = (m.mark_indistinct(idx, a, b,
                                          expect=body.get("text"))
                        if action == "indistinct"
                        else m.keep_passage(idx, a, b))
                if not done:
                    raise ApiFail(409, "not_editable",
                                  "That passage has changed, or can't be "
                                  "edited yet.")
                result = self.payload()
            elif action == "title":
                # The heading of the saved document. Session metadata,
                # not transcript text, so it sits outside undo; empty
                # means no heading at all.
                self.title = " ".join(
                    str(body.get("title") or "").split()) or None
                m.touch_rev()
                m._schedule_autosave()
                result = self.payload()
            elif action == "layout":
                layout = body.get("layout")
                if layout not in ("plain", "qa"):
                    raise ApiFail(400, "bad_request", "Unknown layout.")
                self.layout = layout
                m.touch_rev()
                result = self.payload()
            elif action == "askers":
                # A list of slots, or null to go back to suggesting them.
                askers = body.get("askers")
                if askers is not None:
                    askers = sorted({str(a) for a in askers
                                     if str(a) in m.LETTERS})
                self.askers = askers
                m.touch_rev()
                result = self.payload()
            elif action == "undo":
                m.undo()
                result = self.payload()
            elif action == "redo":
                m.redo()
                result = self.payload()
            else:
                raise ApiFail(404, "unknown_action", action)
            self.broker.publish("review_changed", {"rev": m.rev})
            return result

    def lock_paragraphs(self, lo, hi):
        """Mark paragraphs lo..hi as being re-transcribed, by identity,
        and return their ids so the caller can splice them back even
        after edits elsewhere shift their indices."""
        with self.lock:
            ids = frozenset(self.model.ids[lo:hi + 1])
            self._locked_ids = ids
            self.broker.publish("review_changed",
                                {"rev": self.model.touch_rev()})
            return ids

    def clear_lock(self):
        with self.lock:
            if self._locked_ids:
                self._locked_ids = frozenset()
                self.broker.publish("review_changed",
                                    {"rev": self.model.touch_rev()})

    def apply_retranscribe(self, locked_ids, segments, gap):
        """Splice re-transcribed segments back over the paragraphs whose
        ids are `locked_ids`, wherever they now sit (edits elsewhere may
        have shifted them). One undo step. Refused only if that section
        itself no longer exists as a contiguous block - i.e. it really
        did change - not because unrelated paragraphs were edited."""
        with self.lock:
            try:
                if self.closed:
                    raise ApiFail(409, "closed", "The review has closed.")
                m = self.model
                positions = [i for i, pid in enumerate(m.ids)
                             if pid in locked_ids]
                if (not positions
                        or positions != list(range(positions[0],
                                                    positions[-1] + 1))):
                    raise ApiFail(
                        409, "conflict",
                        "The re-transcribed section itself changed while "
                        "the engine ran - nothing was replaced.")
                paras = paragraphify(segments, gap)
                if not paras:
                    raise ApiFail(
                        422, "no_speech",
                        "No speech was detected in the selected audio - "
                        "the original paragraphs were left unchanged.")
                if not m.replace_runs([(positions[0], positions[-1],
                                        paras)]):
                    raise ApiFail(400, "bad_request", "Bad paragraph range.")
                self.broker.publish("review_changed", {"rev": m.rev})
                return 1
            finally:
                # Whatever the outcome, the section is no longer locked.
                if self._locked_ids:
                    self._locked_ids = frozenset()

    # ----- Autosave -------------------------------------------------------

    def _write_autosave(self, paragraphs, speakers, speaker_names):
        """Crash-recovery snapshot in the exact v0.6.0 schema (port of
        _on_review_autosave, incl. the used-or-renamed names filter)."""
        if self.closed:
            return
        defaults = {L: f"Speaker {L}" for L in TranscriptModel.LETTERS}
        used = {letter for letter in speakers if letter}
        names = {letter: name for letter, name in speaker_names.items()
                 if letter in used or name != defaults.get(letter)}
        _autosave_save({
            "out_path": self.out_path,
            "show_timestamp": self.show_timestamp,
            "colour_speakers": self.colour_speakers,
            "title": self.title,
            "output_format": self.output_format,
            "loaded": self.loaded,
            "diarized": self.diarized,
            "verified_by": self.verified_by,
            "audio_path": self.audio_path,
            "paragraphs": [[list(seg) for seg in para]
                           for para in paragraphs],
            "speakers": list(speakers),
            "speaker_names": names,
            "ts_marks": list(self.model.ts_marks),
            # Which paragraphs a person has claimed, so a restored
            # session still refuses to let the engine overwrite them.
            "text_origin": list(self.model.text_origin),
            "speaker_origin": list(self.model.speaker_origin),
            "suggestions": {str(k): v
                            for k, v in self.model.suggestions.items()},
            "saved_at": time.time(),
        })
        self.broker.publish("autosave", {"saved_at": time.time()})

    # ----- Save / close ----------------------------------------------------

    def _paragraphs_for_writing(self):
        """Apply the per-paragraph timestamp marks for output: amended
        times replace the first segment's start (that is what every
        writer prints), and the hidden list tells the writers which
        stamps to omit."""
        m = self.model
        paras = []
        hidden = []
        for i, para in enumerate(m.paragraphs):
            mark = (m.ts_marks[i] if i < len(m.ts_marks) else None)
            hidden.append(mark == "hidden")
            if isinstance(mark, (int, float)) and para:
                first = para[0]
                para = ([(float(mark), first[1], first[2])]
                        + list(para[1:]))
            paras.append(list(para))
        return paras, hidden

    def _write(self, out_path, speakers, *, output_format=None,
               verified_by=None):
        if self.layout == "qa":
            # Q&A mode saves what the pane shows. The layout prints no
            # speaker labels, so with or without them is the same file.
            return self._write_qa(out_path,
                                  output_format or self.output_format,
                                  verified_by)
        try:
            paragraphs, hide_ts = self._paragraphs_for_writing()
            write_paragraphs_to_file(
                paragraphs, Path(out_path),
                show_timestamp=self.show_timestamp,
                title=self.title,
                output_format=output_format or self.output_format,
                speakers=speakers,
                verified_by=verified_by,
                audio_path=self.audio_path,
                hide_ts=hide_ts,
                # Colour follows the labels: a "no labels" save passes
                # speakers=None, and an unlabelled transcript has
                # nothing to colour by.
                speaker_slots=(list(self.model.speakers)
                               if speakers is not None else None),
                colour_speakers=self.colour_speakers,
                template=current_settings().get("doc_template", "plain"),
                indistinct=self._marked_for_writing(),
            )
        except ImportError as e:
            raise ApiFail(500, "missing_dependency", str(e))
        except ApiFail:
            raise
        except Exception as e:
            raise ApiFail(500, "save_failed", f"{type(e).__name__}: {e}")

    def _marked_for_writing(self):
        """{paragraph index: words} for every paragraph whose markers'
        words are known, for a .docx to keep."""
        m = self.model
        out = {}
        for i in range(len(m.paragraphs)):
            words = m.markers_known(i)
            if any(w is not None for w in words):
                out[i] = words
        return out or None

    def _write_qa(self, out_path, fmt, verified_by):
        """Write the Q&A layout (lock held). A .docx also carries whose
        each paragraph was and who asks, so it reopens in Q&A mode with
        the speakers intact."""
        from .qa import write_qa
        m = self.model
        paragraphs, hide_ts = self._paragraphs_for_writing()
        lay = self._qa()
        rows = lay["rows"]
        names = {s: (m.speaker_names.get(s) or "").strip()
                 or f"Speaker {s}" for s in m.LETTERS}
        meta = {
            "v": 1,
            "speakers": [names[s] if s else None for s in m.speakers],
            "askers": [names[a] for a in lay["askers"]],
            "named": [i for i, r in enumerate(rows) if r["asker"]],
        }
        try:
            write_qa(paragraphs, out_path, [r["mark"] for r in rows],
                     fmt=fmt, title=self.title, note=lay["note"],
                     show_timestamp=self.show_timestamp, hide_ts=hide_ts,
                     verified_by=verified_by,
                     askers=[r["asker"] for r in rows], meta=meta,
                     audio_path=self.audio_path,
                     template=current_settings().get("doc_template",
                                                     "plain"),
                     indistinct=self._marked_for_writing())
        except ImportError as e:
            raise ApiFail(500, "missing_dependency", str(e))
        except Exception as e:
            raise ApiFail(500, "save_failed", f"{type(e).__name__}: {e}")

    def export(self, rev, fmt="pdf", show_timestamp=None,
               verified_by=None, colour_speakers=None, layout=None,
               askers=None):
        """Write a one-off export of the CURRENT reviewed state (with
        labels) alongside the output path, without closing the
        session. Returns the export path.

        layout="qa" writes the numbered question-and-answer layout
        (transcribr/qa.py) as <name>.qa.docx; `askers` names the speaker
        slots who ask the questions, or None to suggest them."""
        with self.lock:
            self._check_rev(rev)
            if self.live:
                # An export mid-run writes the transcript as far as it
                # has got - including the engine's provisional tail -
                # under the ordinary disclaimer, and with a name in the
                # verify field it certifies that a person has checked
                # a transcript that does not exist yet. save() refuses
                # while live for less than this.
                raise ApiFail(409, "still_running",
                              "The transcription is still running, so an "
                              "export would only be part of the "
                              "recording. Wait for it to finish, or "
                              "press Stop.")
            if fmt not in ("pdf", "docx", "txt"):
                raise ApiFail(400, "bad_request", f"Unknown format {fmt}.")
            if layout not in (None, "plain", "qa"):
                raise ApiFail(400, "bad_request", f"Unknown layout {layout}.")
            # The old one-off Q&A export, for a session not in Q&A
            # mode: a .docx beside the transcript. In Q&A mode an
            # ordinary export already writes the layout.
            legacy_qa = layout == "qa" and self.layout != "qa"
            if legacy_qa and fmt == "pdf":
                fmt = "docx"
            if show_timestamp is not None:
                self.show_timestamp = bool(show_timestamp)
            if colour_speakers is not None:
                self.colour_speakers = bool(colour_speakers)
            if verified_by is not None:
                self.verified_by = verified_by.strip() or None
            m = self.model
            for letter in m.LETTERS:
                m.speaker_names[letter] = m.speaker_names.get(
                    letter, "").strip() or f"Speaker {letter}"
            if legacy_qa:
                return self._export_qa(fmt, askers)
            export_path = str(Path(self.out_path).with_suffix(f".{fmt}"))
            if os.path.normcase(export_path) == os.path.normcase(
                    self.out_path):
                export_path = str(
                    Path(self.out_path).with_suffix(f".export.{fmt}"))
            self._write(export_path, m.resolved_speakers(),
                        output_format=fmt,
                        verified_by=self.verified_by)
            self._log_line(f"Exported: {export_path}\n")
            _recent_add(export_path, reviewed=True,
                        verified=bool(self.verified_by))
            self.broker.publish("recents", {})
            return export_path

    def _export_qa(self, fmt, askers):
        """The Q&A export; called with the lock held."""
        from .qa import method_note, qa_marks, qa_roles, write_qa
        m = self.model
        paragraphs, hide_ts = self._paragraphs_for_writing()
        roles, used, method = qa_roles(paragraphs, list(m.speakers),
                                       askers=askers)
        names = [m.speaker_names.get(a, "").strip() or f"Speaker {a}"
                 for a in used]
        export_path = str(Path(self.out_path).with_suffix(f".qa.{fmt}"))
        try:
            write_qa(paragraphs, export_path, qa_marks(roles), fmt=fmt,
                     title=self.title, note=method_note(method, names),
                     show_timestamp=self.show_timestamp, hide_ts=hide_ts,
                     verified_by=self.verified_by)
        except ImportError as e:
            raise ApiFail(500, "missing_dependency", str(e))
        except Exception as e:
            raise ApiFail(500, "save_failed", f"{type(e).__name__}: {e}")
        self._log_line(f"Exported Q&A layout ({method}): {export_path}\n")
        _recent_add(export_path, reviewed=True,
                    verified=bool(self.verified_by))
        self.broker.publish("recents", {})
        return export_path

    def save(self, rev, mode, extra_queue=None, out_format=None,
             show_timestamp=None, verified_by=None, colour_speakers=None):
        """mode: "labels" | "no_labels" | "revision". Returns the final
        out_path. `out_format` and `show_timestamp`, when given (the
        Settings default / the review pane's options), override what
        the run was started with - the output path's extension follows
        the format. `verified_by` switches the appended disclaimer to
        a named certification. Ports _on_review_save /
        _on_review_cancel(fresh) / _on_review_save_revision."""
        with self.lock:
            self._check_rev(rev)
            if self.live:
                # save() ends the session: it writes the file, marks
                # itself closed and clears the autosave. Doing that
                # while the engine is still adding paragraphs threw
                # away the live transcript and everything the reviewer
                # had corrected in it, and the run then opened a fresh
                # session with the engine's own text at the end.
                raise ApiFail(409, "still_running",
                              "The transcription is still running. Wait "
                              "for it to finish, or press Stop.")
            self._check_rev(rev)
            if verified_by is not None:
                self.verified_by = verified_by.strip() or None
            verified_by = self.verified_by
            if out_format in ("txt", "docx", "pdf") \
                    and out_format != self.output_format:
                self.output_format = out_format
                self.out_path = str(
                    Path(self.out_path).with_suffix(f".{out_format}"))
            if show_timestamp is not None:
                self.show_timestamp = bool(show_timestamp)
            if colour_speakers is not None:
                self.colour_speakers = bool(colour_speakers)
            m = self.model
            for letter in m.LETTERS:
                m.speaker_names[letter] = m.speaker_names.get(
                    letter, "").strip() or f"Speaker {letter}"
            if mode == "revision":
                if not self.loaded:
                    raise ApiFail(400, "bad_request",
                                  "Revisions only apply to loaded "
                                  "transcripts.")
                out_path = str(_next_revision_path(Path(self.out_path)))
                self._write(out_path, m.resolved_speakers(),
                            verified_by=verified_by)
                self._log_line(f"Saved revision: {out_path}\n")
            elif mode == "labels":
                out_path = self.out_path
                self._write(out_path, m.resolved_speakers(),
                            verified_by=verified_by)
                self._log_line(
                    f"Wrote {len(m.paragraphs)} paragraphs (with speaker "
                    f"labels)\nto: {out_path}\n")
            elif mode == "no_labels":
                out_path = self.out_path
                self._write(out_path, None, verified_by=verified_by)
                self._log_line(f"Saved without speaker labels: "
                               f"{out_path}\n")
            else:
                raise ApiFail(400, "bad_request", f"Unknown mode {mode}.")

            # A fresh session's safety copy becomes an orphan when the
            # user saved under a different extension - remove it.
            if (self._safety_path
                    and os.path.normcase(self._safety_path)
                    != os.path.normcase(out_path)
                    and Path(self._safety_path).exists()):
                try:
                    Path(self._safety_path).unlink()
                    self._log_line(
                        f"Removed the safety copy: {self._safety_path}\n")
                except OSError:
                    pass

            # Extra formats only accompany full fresh runs (parity).
            # SRT/VTT/TSV follow the REVIEWED text; JSON keeps the raw
            # engine result.
            if (mode in ("labels", "no_labels") and self.result
                    and self.extra_formats and extra_queue is not None):
                _write_extra_formats(
                    _reviewed_result(m.paragraphs, self.result),
                    Path(out_path), self.extra_formats, extra_queue,
                    raw_result=self.result)
            self._finish(reason="saved", out_path=out_path)
            return out_path

    def close_discard(self):
        """Close a LOADED session without saving (the original file is
        untouched). Fresh sessions use save(mode="no_labels") instead."""
        with self.lock:
            if not self.loaded:
                raise ApiFail(400, "bad_request",
                              "A fresh transcription must be saved "
                              "(with or without labels).")
            self._log_line(f"Closed without saving: {self.out_path}\n")
            self._finish(reason="discarded", out_path=None)

    def _finish(self, *, reason, out_path):
        self.closed = True
        self.model.close()
        _autosave_clear()
        if out_path:
            _recent_add(out_path, reviewed=True,
                        verified=bool(self.verified_by))
            self.broker.publish("recents", {})
        self.broker.publish("review_closed",
                            {"reason": reason, "out_path": out_path})

"""Reading transcripts back in - the inverse of writers - and
searching across saved ones."""

from pathlib import Path
import os
import re

from .errors import TranscriptParseError
from .speakers import UNATTRIBUTED_LABEL
from .writers import (
    DOCX_STYLE_SPEAKER, DOCX_STYLE_TEXT, DOCX_STYLE_TITLE,
    INDISTINCT_PROPERTY, read_custom_property,
)


def _indistinct_words(doc):
    """{paragraph index: [words each [indistinct] replaced]} kept in a
    .docx, or {}."""
    try:
        import json as _json
        raw = read_custom_property(doc, INDISTINCT_PROPERTY)
        return {int(k): list(v) for k, v in (_json.loads(raw) if raw else {}).items()}
    except Exception:
        return {}
from .platformutil import _recent_load


# Matches "[MM:SS]" or "[H:MM:SS]" at the start of a line (paragraph body).
_TS_PREFIX_RE = re.compile(r"^\s*\[(\d+:)?(\d+):(\d+)\]\s*")

# A stamp the writer put there, as opposed to a paragraph that merely
# opens with something shaped like one ("[00:12] was the time on the
# clock"). render() has separated a real stamp from the body with two
# spaces since the first commit, and _write_docx with a tab, so the
# separator is the one thing that tells them apart. Getting it wrong ate
# the opening words of the paragraph.
_TS_WRITTEN_RE = re.compile(r"^\s*\[(?:\d+:)?\d+:\d+\](?:[ ]{2,}|\t)")

# Matches a Transcribr disclaimer line. We strip these so they don't
# come back as paragraphs.
_DISCLAIMER_RE = re.compile(
    r"^Transcribed using Transcribr",
    re.IGNORECASE,
)


def _parse_timestamp_prefix(line):
    """Return (start_seconds, body_text) if `line` begins with [MM:SS] or
    [H:MM:SS], else (None, line). Used by both .txt and .docx parsers."""
    m = _TS_PREFIX_RE.match(line)
    if not m:
        return None, line
    h = int(m.group(1)[:-1]) if m.group(1) else 0
    mm = int(m.group(2))
    ss = int(m.group(3))
    return h * 3600 + mm * 60 + ss, line[m.end():]


# --- searching saved transcripts --------------------------------------
#
# Once there are more than a handful of transcripts, the questions stop
# being "open the last one" and become "which recording mentioned the
# white van?" and "what have I already transcribed for this brief?".
# Neither is answerable from a list of file names.
#
# The transcripts are already on disk in a format this app can parse, so
# this is an index over files that already exist rather than a new store
# to keep in step. Candidates are the recent list plus anything beside
# those files that looks like a transcript - so the other recordings in
# a matter folder are searchable without having been opened first.

SEARCH_FILE_LIMIT = 400
SEARCH_HITS_PER_FILE = 20
_TRANSCRIPT_GLOBS = ("*.transcript.docx", "*.transcript.txt")


def _search_candidates(extra_paths=()):
    """Transcript files worth searching: everything in the recent list,
    plus their folder-mates. Ordered, deduped, and capped."""
    seen, out = set(), []

    def add(path):
        try:
            resolved = str(Path(path).resolve())
        except OSError:
            return
        if resolved in seen or len(out) >= SEARCH_FILE_LIMIT:
            return
        seen.add(resolved)
        out.append(resolved)

    folders = []
    for item in list(extra_paths) + [i["path"] for i in _recent_load()]:
        add(item)
        try:
            parent = Path(item).parent
        except (OSError, ValueError):
            continue
        if parent not in folders:
            folders.append(parent)

    for folder in folders:
        for pattern in _TRANSCRIPT_GLOBS:
            try:
                for match in sorted(folder.glob(pattern)):
                    add(match)
            except OSError:
                continue
    return out


def search_transcripts(query, paths=None, limit=SEARCH_FILE_LIMIT):
    """Find `query` across saved transcripts.

    Returns {"results": [...], "searched": n, "unreadable": m}, one entry
    per file that matched, each carrying the paragraphs that matched and
    the timestamp to seek to. Case-insensitive substring matching: the
    point is finding a phrase someone half-remembers, not a regex.
    """
    query = (query or "").strip()
    if not query:
        return {"results": [], "searched": 0, "unreadable": 0}
    needle = query.lower()

    results, searched, unreadable = [], 0, 0
    for path in (paths if paths is not None else _search_candidates())[:limit]:
        try:
            parsed = read_paragraphs_from_file(path)
        except TranscriptParseError:
            unreadable += 1
            continue
        except Exception:
            unreadable += 1
            continue
        searched += 1

        hits = []
        for index, para in enumerate(parsed["paragraphs"]):
            body = " ".join(seg[2] for seg in para)
            if needle not in body.lower():
                continue
            hits.append({
                "index": index,
                "start": float(para[0][0]) if para else 0.0,
                "text": body,
                "speaker": (parsed["speakers"][index]
                            if index < len(parsed["speakers"]) else None),
            })
            if len(hits) >= SEARCH_HITS_PER_FILE:
                break
        if hits:
            results.append({
                "path": path,
                "name": Path(path).name,
                "folder": Path(path).parent.name,
                "folder_path": str(Path(path).parent),
                "title": parsed.get("title"),
                "hits": hits,
                "total_hits": len(hits),
            })

    # Group by folder in the caller's order: matters are folders, and
    # that is how someone looking for "the Smith brief" thinks.
    results.sort(key=lambda r: (r["folder"].lower(), r["name"].lower()))
    return {"results": results, "searched": searched,
            "unreadable": unreadable}


def read_paragraphs_from_file(path):
    """Read a Transcribr-written .txt or .docx and return a dict with:
        paragraphs    - list of paragraphs in the format used by the
                        review pane (each paragraph is a list of
                        (start, end, text) tuples; we synthesise one
                        segment per paragraph since the original
                        segmentation isn't recoverable)
        speakers      - list of speaker name strings (or None) parallel
                        to paragraphs
        title         - the title at the top of the document, or None
        show_timestamp - True if the file contained timestamps, False
                        if not (informs how it should be re-rendered)

    Raises TranscriptParseError on any failure with a message suitable
    for showing the user.
    """
    path = Path(path)
    if not path.exists():
        raise TranscriptParseError(f"File not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".txt":
        parsed = _parse_qa_txt(path) or _parse_txt_transcript(path)
    elif suffix == ".docx":
        parsed = _parse_qa_docx(path) or _parse_docx_transcript(path)
    else:
        raise TranscriptParseError(
            f"Unsupported file extension: {suffix}. Only .txt and .docx are supported."
        )
    _infer_paragraph_end_times(parsed["paragraphs"])
    return parsed


# A saved Q&A .txt row opening with its mark, padded to six columns; a
# row continuing the same side has a blank mark instead.
_QA_TXT_MARKED_RE = re.compile(r"^(Q\d+|A)\s+\S")
_QA_NOTE_RE = re.compile(r"^Q and A (follow|were suggested)")
_QA_ASKER_RE = re.compile(r"^\(([^()]{1,60})\) ")

# What a reopened Q&A .txt calls the two sides when it cannot tell who
# was speaking: the layout keeps the role but not the person.
QA_QUESTIONER = "Questioner"
QA_ANSWERER = "Answerer"


def _qa_row_body(body, saw):
    """(start, body, stamped) for a row body that may open with a
    stamp."""
    if _TS_WRITTEN_RE.match(body):
        ts, rest = _parse_timestamp_prefix(body)
        if ts is not None:
            saw[0] = True
            return ts, rest.strip(), True
    return None, body.strip(), False


def _qa_result(rows, title, saw_timestamp, askers, **extra):
    """rows: (start | None, body, speaker name | None)."""
    paragraphs, speakers, ts_hidden = [], [], []
    for start, body, who in rows:
        if not body:
            continue
        if start is None:
            start = paragraphs[-1][0][0] if paragraphs else 0.0
            ts_hidden.append(True)
        else:
            ts_hidden.append(False)
        paragraphs.append([(start, start + 1.0, body)])
        speakers.append(who)
    if not paragraphs:
        return None
    return {"paragraphs": paragraphs, "speakers": speakers,
            "title": title, "show_timestamp": saw_timestamp,
            "ts_hidden": ts_hidden, "layout": "qa",
            "askers_names": askers, **extra}


def _parse_qa_txt(path):
    """A Q&A layout .txt (transcribr/qa.py), or None if it isn't one.
    The file does not say who each speaker was, only which side: every
    answer comes back as one speaker, and questions as whoever was
    named at the last change of questioner."""
    lines = path.read_text(encoding="utf-8").splitlines()
    marked = sum(1 for ln in lines if _QA_TXT_MARKED_RE.match(ln))
    if marked < 2:
        return None
    title = None
    body_start = 0
    if lines and lines[0].strip() and not _QA_TXT_MARKED_RE.match(lines[0]) \
            and not lines[0].startswith(" ") \
            and not _QA_NOTE_RE.match(lines[0]):
        title = lines[0].strip()
        body_start = 1
    rows, askers = [], []
    role, asker, saw = None, None, [False]
    for ln in lines[body_start:]:
        if not ln.strip() or _QA_NOTE_RE.match(ln) \
                or _DISCLAIMER_RE.match(ln):
            continue
        m = re.match(r"^(Q\d+|A)(?=\s)", ln)
        if m:
            role = "Q" if m.group(1) != "A" else "A"
            body = ln[len(m.group(1)):]
        elif ln.startswith(" "):
            body = ln
        else:
            return None         # not the layout qa.py writes
        start, body, _ = _qa_row_body(body.strip(), saw)
        if re.match(r"^\[No audio\b", body, re.IGNORECASE):
            rows.append((start, body, None))
            role = None
            continue
        who = None
        if role == "Q":
            named = _QA_ASKER_RE.match(body)
            if named and m:
                asker = named.group(1)
                body = body[named.end():]
            who = asker or QA_QUESTIONER
            if who not in askers:
                askers.append(who)
        elif role == "A":
            who = QA_ANSWERER
        rows.append((start, body, who))
    return _qa_result(rows, title, saw[0], askers)


def _parse_qa_docx(path):
    """A Q&A layout .docx carrying Transcribr's custom property, or
    None. The property records whose each row was; if the rows no
    longer line up with it (edited in Word), the sides are kept and the
    people fall back as for a .txt."""
    try:
        from docx import Document
        from .qa import (QA_PROPERTY, read_custom_property, split_qa_mark,
                         strip_asker)
        import json as _json
        doc = Document(str(path))
        raw = read_custom_property(doc, QA_PROPERTY)
        meta = _json.loads(raw) if raw else None
    except Exception:
        return None
    if not meta:
        return None
    paras = list(doc.paragraphs)
    title = None
    if paras and (paras[0].style.name or "") == DOCX_STYLE_TITLE:
        title = paras[0].text.strip() or None
    texts = [p.text for p in paras
             if (p.style.name or "").startswith(DOCX_STYLE_TEXT)
             and p.text.strip()]
    who = meta.get("speakers") or []
    named = set(meta.get("named") or [])
    aligned = len(who) == len(texts)
    rows, saw = [], [False]
    role, asker = None, None
    askers = list(meta.get("askers") or [])
    for i, text in enumerate(texts):
        mark, rest = split_qa_mark(text)
        start, body, _ = _qa_row_body(rest, saw)
        if aligned:
            name = who[i]
            if i in named:
                body = strip_asker(body, name)
            rows.append((start, body, name))
            continue
        if mark:
            role = "A" if mark == "A" else "Q"
        hit = _QA_ASKER_RE.match(body)
        if role == "Q" and hit and mark:
            asker = hit.group(1)
            body = body[hit.end():]
        name = ((asker or QA_QUESTIONER) if role == "Q"
                else QA_ANSWERER if role == "A" else None)
        rows.append((start, body, name))
    extra = {}
    try:
        meta_c = (_json.loads(doc.core_properties.comments or "")
                  .get("transcribr") or {})
        extra["audio_path"] = meta_c.get("audio")
        rel = meta_c.get("audio_rel")
        if rel:
            extra["audio_path_rel"] = os.path.normpath(
                str(Path(path).parent / rel))
        extra["verified_by"] = meta_c.get("verified_by")
    except Exception:
        pass
    extra["indistinct"] = _indistinct_words(doc)
    return _qa_result(rows, title, saw[0], askers, **extra)


def _infer_paragraph_end_times(paragraphs):
    """Saved transcripts only record each paragraph's start time, so the
    parsers synthesise a placeholder 1-second span. Stretch each
    paragraph's end out to the next paragraph's start so the span covers
    the actual speech - audio playback depends on this. The last
    paragraph's true end is unknowable from the file; it keeps the
    placeholder span and playback treats it as open-ended."""
    for i in range(len(paragraphs) - 1):
        para = paragraphs[i]
        nxt = paragraphs[i + 1]
        if not para or not nxt:
            continue
        next_start = nxt[0][0]
        start, end, text = para[-1]
        if next_start > end:
            para[-1] = (start, next_start, text)


def _parse_txt_transcript(path):
    """Parse a .txt file written by render(). Format:

        <title (optional)>

        SPEAKER NAME:
        [MM:SS]  body text

        [MM:SS]  body text from same speaker (no label since unchanged)

        OTHER SPEAKER:
        [MM:SS]  ...

        Transcribed using Transcribr ... (disclaimer line - skipped)

    Blocks are separated by blank lines. We tolerate transcripts that
    omit timestamps (text-only output) and transcripts that omit
    speaker labels (just text and timestamps)."""
    text = path.read_text(encoding="utf-8")
    # Split into blocks at blank lines. Strip trailing whitespace per block
    # but preserve internal newlines (a block is "SPEAKER:\n[MM:SS]  body").
    blocks = [b.strip("\n") for b in re.split(r"\n[ \t]*\n", text) if b.strip()]
    if not blocks:
        raise TranscriptParseError("File is empty.")

    title = None
    paragraphs = []
    speakers = []
    ts_hidden = []
    current_speaker = None
    saw_timestamp = False  # tracks whether we ever saw [MM:SS]

    # First block could be the title. A title looks like a single line
    # that isn't a speaker label and doesn't start with a timestamp.
    #
    # Nothing in the file distinguishes a bare title from a first
    # paragraph that happens to carry neither a speaker label nor a
    # timestamp, so render() now writes the unattributed marker above
    # such a paragraph and this test never has to guess about a file
    # this version wrote. For the ones already on disk it still has to,
    # and the two mistakes are not equally bad: reading a title as a
    # paragraph shows the user a stray line they can delete, while
    # reading a paragraph as a title loses evidence without saying so.
    # So prose wins the tie - a block ending in sentence punctuation is
    # taken as a paragraph, which titles almost never are.
    first = blocks[0]
    first_lines = first.splitlines()
    looks_like_speaker = (len(first_lines) >= 1
                          and first_lines[0].rstrip().endswith(":")
                          and not _TS_PREFIX_RE.match(first_lines[0]))
    has_timestamp = bool(_TS_PREFIX_RE.match(first_lines[0]))
    reads_as_prose = first.rstrip().endswith((".", "!", "?", "…"))
    if (not looks_like_speaker and not has_timestamp
            and not reads_as_prose and len(first_lines) <= 3):
        # Treat as title and consume.
        title = first
        blocks = blocks[1:]

    for n, block in enumerate(blocks):
        # Only the last block can be the disclaimer the writer appends.
        # Matching it anywhere meant a transcript that quoted the line -
        # a transcript of someone reading one out, most obviously - lost
        # that paragraph.
        if n == len(blocks) - 1 and _DISCLAIMER_RE.match(block):
            continue
        lines = block.splitlines()
        # Speaker label is the first line iff it ends with ":" and isn't
        # a timestamp.
        if lines and lines[0].rstrip().endswith(":") and not _TS_PREFIX_RE.match(lines[0]):
            label = lines[0].rstrip().rstrip(":").strip()
            # Recognise the explicit "no speaker" marker we emit on
            # transitions from a named speaker back to unattributed.
            current_speaker = None if label == UNATTRIBUTED_LABEL else label
            lines = lines[1:]
        if not lines:
            # Speaker line on its own with no body. Skip; we'll attach
            # the speaker to the next block's body.
            continue
        # The remaining lines are paragraph body. Look for timestamp prefix
        # on the first content line.
        first_content = lines[0]
        ts, after_ts = _parse_timestamp_prefix(first_content)
        if ts is not None and not _TS_WRITTEN_RE.match(first_content):
            # Shaped like a stamp but not written as one: it is the
            # paragraph's own first words.
            ts, after_ts = None, first_content
        if ts is not None:
            saw_timestamp = True
            start_t = ts
            lines[0] = after_ts.lstrip()
        else:
            # No stamp on this paragraph: carry the previous start
            # forward so playback lands nearby. If the file has stamps
            # elsewhere, the missing one was hidden on purpose.
            start_t = (paragraphs[-1][0][0] if paragraphs else 0.0)
        body = " ".join(line.strip() for line in lines if line.strip())
        if not body:
            continue
        # Synthesise a single segment for this paragraph.
        paragraphs.append([(start_t, start_t + 1.0, body)])
        speakers.append(current_speaker)
        ts_hidden.append(ts is None)

    if not paragraphs:
        raise TranscriptParseError(
            "No transcript content found. The file may not be a "
            "Transcribr-produced transcript."
        )
    return {
        "paragraphs": paragraphs,
        "speakers": speakers,
        "title": title,
        "show_timestamp": saw_timestamp,
        "ts_hidden": ts_hidden,
    }


def _parse_docx_transcript(path):
    """Parse a .docx file written by _write_docx().

    We rely on paragraph styles to identify what each paragraph is:
        - "Title" / first bold paragraph -> title
        - "Transcribr Speaker" (or "... 1".."... 9") -> speaker line
        - "Transcribr Text" (or "... 1".."... 9") -> body paragraph
        - everything else -> ignored (disclaimer, blank spacers, etc.)

    0.9.14 and earlier named these two styles "SpeakerLabel" and
    "Transcript", and those are still recognised: an old transcript has
    to keep reopening.

    Word's "Clear Formatting" or paste operations can lose the styles.
    For robustness, we also fall back to text-based heuristics if a
    paragraph has no recognised style: lines ending with ':' look like
    speakers, lines starting with [MM:SS] look like body."""
    try:
        from docx import Document
    except ImportError:
        raise TranscriptParseError(
            "Cannot read .docx files: the 'python-docx' package is not "
            "installed."
        )
    try:
        doc = Document(str(path))
    except Exception as e:
        raise TranscriptParseError(f"Could not open .docx file: {e}")

    title = None
    paragraphs = []
    speakers = []
    current_speaker = None
    saw_timestamp = False
    ts_hidden = []

    docx_paragraphs = list(doc.paragraphs)
    if not docx_paragraphs:
        raise TranscriptParseError("Document is empty.")

    # First paragraph is the title if it says so by style, or - for the
    # documents 0.9.14 wrote, where the bold sat on the run rather than
    # on a style - if it is bold and looks like neither a speaker label
    # nor a body line.
    first = docx_paragraphs[0]
    first_text = first.text.strip()
    if first_text:
        try:
            first_style = (first.style.name or "").strip()
        except Exception:
            first_style = ""
        runs_bold = all(
            (r.bold or False) for r in first.runs if r.text.strip()
        ) if first.runs else False
        looks_speaker = first_text.endswith(":")
        has_ts = bool(_TS_PREFIX_RE.match(first_text))
        if first_style == DOCX_STYLE_TITLE or (
                runs_bold and not looks_speaker and not has_ts):
            title = first_text
            docx_paragraphs = docx_paragraphs[1:]

    last_with_text = max(
        (i for i, q in enumerate(docx_paragraphs) if q.text.strip()),
        default=-1)
    for i, p in enumerate(docx_paragraphs):
        text = p.text.strip()
        if not text:
            continue
        # Only the trailing disclaimer is the writer's own; a body
        # paragraph that quotes it is still a body paragraph.
        if i == last_with_text and _DISCLAIMER_RE.match(text):
            continue
        style_name = ""
        try:
            style_name = (p.style.name or "").strip()
        except Exception:
            pass

        # The per-speaker and per-text styles are the base name plus a
        # slot number, so match on the prefix rather than listing all
        # nine. "SpeakerLabel"/"Transcript" are what 0.9.14 called them.
        is_speaker_style = (style_name == "SpeakerLabel"
                            or style_name.startswith(DOCX_STYLE_SPEAKER))
        is_text_style = (style_name == "Transcript"
                         or style_name.startswith(DOCX_STYLE_TEXT))

        # Text heuristics are a fallback for documents whose styles Word
        # has stripped - they must not override a style that is right
        # there. Applying them unconditionally meant a body paragraph
        # ending in a colon ("and then he turned to me and said:") was
        # read as a speaker label and dropped, taking the evidence with
        # it; in spoken testimony that is not a rare way to end a
        # sentence.
        if is_speaker_style or (not is_text_style
                                and text.endswith(":")
                                and not _TS_PREFIX_RE.match(text)):
            label = text.rstrip(":").strip()
            current_speaker = None if label == UNATTRIBUTED_LABEL else label
            continue

        # Body paragraph. In python-docx, tabs in run text appear as
        # "\t" characters - and the writer has always separated the
        # stamp from the body with one, so on a styled body that tab is
        # what tells a real stamp from a paragraph that merely opens
        # with something shaped like one ("[00:12] was the time on the
        # clock"). Without the distinction the opening words were eaten.
        stamped = _TS_PREFIX_RE.match(text) and "\t" in text
        body_text = text.replace("\t", " ")
        ts, after_ts = _parse_timestamp_prefix(body_text)
        if ts is not None and (stamped or not is_text_style):
            saw_timestamp = True
            start_t = ts
            body = after_ts.strip()
        else:
            # Deliberately hidden stamp (or stamp-less output): carry
            # the previous start forward so playback lands nearby.
            ts = None
            start_t = (paragraphs[-1][0][0] if paragraphs else 0.0)
            body = body_text.strip()
        if not body:
            continue
        paragraphs.append([(start_t, start_t + 1.0, body)])
        speakers.append(current_speaker)
        ts_hidden.append(ts is None)

    if not paragraphs:
        raise TranscriptParseError(
            "No transcript content found. The file may not be a "
            "Transcribr-produced transcript."
        )

    # The writer embeds Transcribr metadata (comments): the source
    # audio for playback (absolute and/or transcript-relative, the
    # latter surviving a moved folder), and who verified the
    # transcript.
    embedded_audio = None
    embedded_audio_rel = None
    embedded_verified = None
    try:
        import json as _json
        meta = (_json.loads(doc.core_properties.comments or "")
                .get("transcribr") or {})
        embedded_audio = meta.get("audio")
        rel = meta.get("audio_rel")
        if rel:
            embedded_audio_rel = os.path.normpath(
                str(Path(path).parent / rel))
        embedded_verified = meta.get("verified_by")
    except Exception:
        pass

    return {
        "paragraphs": paragraphs,
        "speakers": speakers,
        "title": title,
        "show_timestamp": saw_timestamp,
        "ts_hidden": ts_hidden,
        "indistinct": _indistinct_words(doc),
        "audio_path": embedded_audio,
        "audio_path_rel": embedded_audio_rel,
        "verified_by": embedded_verified,
    }

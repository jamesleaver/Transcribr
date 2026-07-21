import { memo, useEffect, useRef, useState } from "react";
import { useReview, type ReviewParagraph } from "../../state/reviewStore";

// The transcript document: one row per paragraph in three columns
// (speaker chip / timestamp / body), memoized and keyed by stable id.
// Semantics ported from the Tk pane: a speaker label renders only when
// the speaker CHANGES from the previous paragraph, a named->None
// transition shows "[Unattributed]", and row background priority is
// editing > selected > speaker colour. Confidence + search highlights
// are inline spans.

const WORD_CHARS = /[\p{L}\p{N}'’-]/u;

/** Parse "MM:SS", "H:MM:SS" or bare seconds into seconds, else null. */
function parseTimestamp(text: string): number | null {
  const t = text.trim().replace(/^\[|\]$/g, "");
  if (!t) return null;
  if (/^\d+(\.\d+)?$/.test(t)) return parseFloat(t);
  const m = t.match(/^(?:(\d+):)?(\d{1,2}):(\d{2})$/);
  if (!m) return null;
  const h = m[1] ? parseInt(m[1], 10) : 0;
  return h * 3600 + parseInt(m[2], 10) * 60 + parseInt(m[3], 10);
}

function formatTimestamp(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h > 0
    ? `[${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}]`
    : `[${m}:${String(sec).padStart(2, "0")}]`;
}

/** Cut the body into styled spans: search highlight wins over confidence. */
function renderBody(
  body: string,
  conf: [number, number, "low" | "med"][],
  search: { start: number; end: number } | null,
) {
  type Mark = { start: number; end: number; cls: string; pri: number };
  const marks: Mark[] = conf.map(([s, e, level]) => ({
    start: s,
    end: e,
    cls: level === "low" ? "conf-low" : "conf-med",
    pri: 1,
  }));
  if (search) marks.push({ ...search, cls: "search-hit", pri: 2 });
  if (marks.length === 0) return body;

  const cuts = new Set<number>([0, body.length]);
  for (const m of marks) {
    cuts.add(Math.max(0, m.start));
    cuts.add(Math.min(body.length, m.end));
  }
  const points = [...cuts].sort((a, b) => a - b);
  const out: React.ReactNode[] = [];
  for (let i = 0; i < points.length - 1; i++) {
    const [a, b] = [points[i], points[i + 1]];
    if (a >= b) continue;
    const active = marks
      .filter((m) => m.start <= a && m.end >= b)
      .sort((x, y) => y.pri - x.pri)[0];
    const text = body.slice(a, b);
    out.push(
      active ? (
        <span
          key={a}
          style={{
            background:
              active.cls === "search-hit"
                ? "var(--search-bg)"
                : active.cls === "conf-low"
                  ? "var(--conf-low)"
                  : "var(--conf-med)",
            borderRadius: 3,
          }}
        >
          {text}
        </span>
      ) : (
        text
      ),
    );
  }
  return out;
}

/** Caret character offset within the body element at a mouse position. */
function caretOffset(container: HTMLElement, e: React.MouseEvent): number | null {
  type CaretPos = { offsetNode: Node; offset: number };
  const docAny = document as Document & {
    caretPositionFromPoint?: (x: number, y: number) => CaretPos | null;
    caretRangeFromPoint?: (x: number, y: number) => Range | null;
  };
  let node: Node | null = null;
  let off = 0;
  if (docAny.caretPositionFromPoint) {
    const pos = docAny.caretPositionFromPoint(e.clientX, e.clientY);
    if (!pos) return null;
    node = pos.offsetNode;
    off = pos.offset;
  } else if (docAny.caretRangeFromPoint) {
    const range = docAny.caretRangeFromPoint(e.clientX, e.clientY);
    if (!range) return null;
    node = range.startContainer;
    off = range.startOffset;
  }
  if (!node) return null;
  // Sum the lengths of text nodes before the hit node.
  let total = 0;
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const t = walker.currentNode;
    if (t === node) return total + off;
    total += (t.textContent ?? "").length;
  }
  return null;
}

/** The clickable timestamp cell: shows the effective stamp; clicking
 *  opens a small editor to amend the time, hide the stamp, or put it
 *  back. Saved documents follow whatever is chosen here. */
function TimestampCell({
  para,
  index,
}: {
  para: ReviewParagraph;
  index: number;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const hidden = para.ts === "hidden";
  const amended = typeof para.ts === "number";
  const effective = amended ? (para.ts as number) : para.start;

  const apply = () => {
    const parsed = parseTimestamp(draft);
    if (parsed !== null) {
      void useReview.getState().setTimestamp(index, parsed);
    }
    setOpen(false);
  };

  return (
    <div className="relative pt-0.5">
      <button
        className="rounded font-mono text-[11px] hover:underline"
        style={{
          color: "var(--timestamp-fg)",
          opacity: hidden ? 0.45 : 1,
          fontStyle: amended ? "italic" : undefined,
        }}
        title={
          hidden
            ? "Timestamp hidden in saved documents — click to change"
            : amended
              ? "Amended timestamp — click to change"
              : "Click to amend or hide this timestamp"
        }
        onClick={(e) => {
          e.stopPropagation();
          useReview.getState().select(index);
          setDraft(formatTimestamp(effective).replace(/^\[|\]$/g, ""));
          setOpen((v) => !v);
        }}
      >
        {hidden ? "[–:––]" : formatTimestamp(effective)}
      </button>
      {open && (
        <div
          className="absolute left-0 top-6 z-10 w-44 rounded-lg border border-edge bg-surface p-2 shadow-lg"
          onClick={(e) => e.stopPropagation()}
        >
          <input
            className="w-full rounded border border-edge bg-surface px-2 py-1 font-mono text-xs focus:border-accent focus:outline-none"
            value={draft}
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") apply();
              if (e.key === "Escape") setOpen(false);
            }}
          />
          <div className="mt-1.5 flex flex-wrap gap-1">
            <button
              className="rounded border border-edge px-2 py-0.5 text-[11px] font-medium hover:bg-surface-2"
              onClick={apply}
            >
              Apply
            </button>
            <button
              className="rounded border border-edge px-2 py-0.5 text-[11px] font-medium hover:bg-surface-2"
              onClick={() => {
                void useReview
                  .getState()
                  .setTimestamp(index, hidden ? null : "hidden");
                setOpen(false);
              }}
            >
              {hidden ? "Show" : "Hide"}
            </button>
            {(amended || hidden) && (
              <button
                className="rounded border border-edge px-2 py-0.5 text-[11px] font-medium hover:bg-surface-2"
                onClick={() => {
                  void useReview.getState().setTimestamp(index, null);
                  setOpen(false);
                }}
              >
                Reset
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

const Row = memo(function Row({
  para,
  index,
  prevSpeaker,
  showTimestamp,
}: {
  para: ReviewParagraph;
  index: number;
  prevSpeaker: string | null;
  showTimestamp: boolean;
}) {
  const selected = useReview((s) => s.selected === index);
  const inRange = useReview(
    (s) =>
      s.selRange !== null
      && index >= s.selRange.from
      && index <= s.selRange.to,
  );
  const editing = useReview((s) => s.editing === index);
  const draft = useReview((s) => (s.editing === index ? s.editingDraft : ""));
  const showConf = useReview((s) => s.showConfidence);
  const names = useReview((s) => s.doc?.speaker_names ?? {});
  const hit = useReview((s) =>
    s.searchHit?.index === index
      ? { start: s.searchHit.start, end: s.searchHit.end }
      : null,
  );
  const ref = useRef<HTMLDivElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (selected) ref.current?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  useEffect(() => {
    if (editing && textRef.current) {
      const el = textRef.current;
      el.focus();
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    }
  }, [editing]);

  const changed = para.speaker !== prevSpeaker;
  const label = changed
    ? para.speaker
      ? (names[para.speaker] ?? `Speaker ${para.speaker}`)
      : prevSpeaker
        ? "[Unattributed]"
        : null
    : null;

  const rowBg = editing
    ? "var(--editing-bg)"
    : selected || inRange
      ? "var(--selected-bg)"
      : para.speaker
        ? `var(--speaker-${para.speaker})`
        : undefined;

  const onDoubleClick = (e: React.MouseEvent) => {
    if (editing || para.locked) return;
    const container = e.currentTarget as HTMLElement;
    let off = caretOffset(container, e);
    if (off === null) return;
    // Snap left to the start of the clicked word (Tk splits at words).
    const body = para.body;
    while (off > 0 && WORD_CHARS.test(body[off - 1])) off -= 1;
    void useReview.getState().split(index, off);
  };

  const onClick = (e: React.MouseEvent) => {
    const st = useReview.getState();
    if (st.editing !== null && st.editing !== index) {
      void st.commitPendingEdit();
    }
    if (e.shiftKey) {
      st.selectRangeTo(index);
    } else {
      st.select(index);
    }
  };

  return (
    <div
      ref={ref}
      onClick={onClick}
      title={
        para.locked
          ? "Being re-transcribed — this section updates when the engine finishes."
          : para.suspect
            ? "This looks like repeated or low-confidence text — consider re-transcribing it (Fix a section)."
            : undefined
      }
      className="grid cursor-default grid-cols-[130px_64px_1fr] gap-3 rounded-lg px-3 py-1.5 transition-opacity"
      style={{
        background: rowBg,
        opacity: para.locked ? 0.45 : 1,
        boxShadow: para.locked
          ? "inset 3px 0 0 0 var(--accent)"
          : para.suspect
            ? "inset 3px 0 0 0 #d97706"
            : undefined,
      }}
    >
      <div className="pt-0.5">
        {label && (
          <span
            className="inline-block max-w-full truncate rounded-md px-2 py-0.5 text-xs font-semibold"
            style={{
              background: para.speaker
                ? `var(--speaker-${para.speaker})`
                : "var(--surface-2)",
              color: "var(--badge-fg)",
              filter: para.speaker ? "brightness(0.92)" : undefined,
            }}
            title={label}
          >
            {label}
          </span>
        )}
      </div>
      {showTimestamp ? (
        <TimestampCell para={para} index={index} />
      ) : (
        <div />
      )}
      {editing ? (
        <textarea
          ref={textRef}
          className="w-full resize-none rounded border-none bg-transparent p-0 text-sm leading-relaxed outline-none"
          style={{ color: "var(--text-fg)" }}
          value={draft}
          rows={1}
          onChange={(e) => {
            useReview.getState().setEditingDraft(e.target.value);
            e.target.style.height = "auto";
            e.target.style.height = `${e.target.scrollHeight}px`;
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void useReview.getState().commitPendingEdit();
            } else if (e.key === "Escape") {
              e.preventDefault();
              useReview.getState().cancelEdit();
            }
          }}
        />
      ) : (
        <div
          className="text-sm leading-relaxed"
          style={{ color: "var(--text-fg)" }}
          onDoubleClick={onDoubleClick}
        >
          {renderBody(para.body, showConf ? para.conf : [], hit)}
        </div>
      )}
    </div>
  );
});

export default function TranscriptDoc() {
  const doc = useReview((s) => s.doc);
  if (!doc) return null;
  return (
    <div
      className="flex-1 overflow-y-auto rounded-xl border border-edge p-4"
      style={{ background: "var(--text-bg)" }}
    >
      {doc.paragraphs.map((p, i) => (
        <Row
          key={p.id}
          para={p}
          index={i}
          prevSpeaker={i > 0 ? doc.paragraphs[i - 1].speaker : null}
          showTimestamp={doc.show_timestamp}
        />
      ))}
    </div>
  );
}

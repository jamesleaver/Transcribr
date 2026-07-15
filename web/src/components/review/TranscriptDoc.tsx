import { memo, useEffect, useRef } from "react";
import { useReview, type ReviewParagraph } from "../../state/reviewStore";

// The transcript document: one row per paragraph in three columns
// (speaker chip / timestamp / body), memoized and keyed by stable id.
// Semantics ported from the Tk pane: a speaker label renders only when
// the speaker CHANGES from the previous paragraph, a named->None
// transition shows "[Unattributed]", and row background priority is
// editing > selected > speaker colour. Confidence + search highlights
// are inline spans.

const WORD_CHARS = /[\p{L}\p{N}'’-]/u;

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
    : selected
      ? "var(--selected-bg)"
      : para.speaker
        ? `var(--speaker-${para.speaker})`
        : undefined;

  const onDoubleClick = (e: React.MouseEvent) => {
    if (editing) return;
    const container = e.currentTarget as HTMLElement;
    let off = caretOffset(container, e);
    if (off === null) return;
    // Snap left to the start of the clicked word (Tk splits at words).
    const body = para.body;
    while (off > 0 && WORD_CHARS.test(body[off - 1])) off -= 1;
    void useReview.getState().split(index, off);
  };

  const onClick = () => {
    const st = useReview.getState();
    if (st.editing !== null && st.editing !== index) {
      void st.commitPendingEdit();
    }
    st.select(index);
  };

  return (
    <div
      ref={ref}
      onClick={onClick}
      className="grid cursor-default grid-cols-[130px_64px_1fr] gap-3 rounded-lg px-3 py-1.5"
      style={{ background: rowBg }}
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
      <div
        className="pt-0.5 font-mono text-[11px]"
        style={{ color: "var(--timestamp-fg)" }}
      >
        {showTimestamp ? formatTimestamp(para.start) : ""}
      </div>
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

import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useReview, type ReviewParagraph } from "../../state/reviewStore";

// The transcript document: one row per paragraph in three columns
// (speaker chip / timestamp / body), memoized and keyed by stable id.
// Semantics ported from the Tk pane: a speaker label renders only when
// the speaker CHANGES from the previous paragraph, a named->None
// transition shows "[Unattributed]", and row background priority is
// editing > selected > speaker colour. Confidence + search highlights
// are inline spans.

// The writers pin Courier New for the saved document; the pane uses
// the same family so the measure and the line breaks read the way
// the file will.
const DOC_FONT = '"Courier New", Courier, ui-monospace, monospace';

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
  unsure: { start: number; end: number } | null = null,
  markers: [number, number, string, number][] = [],
  index = -1,
) {
  type Mark = { start: number; end: number; cls: string; pri: number; ordinal?: number };
  const marks: Mark[] = conf.map(([s, e, level]) => ({
    start: s,
    end: e,
    cls: level === "low" ? "conf-low" : "conf-med",
    pri: 1,
  }));
  if (search) marks.push({ ...search, cls: "search-hit", pri: 2 });
  markers.forEach(([s, e], k) =>
    marks.push({ start: s, end: e, cls: "marker", pri: 4, ordinal: k }));
  // (ordinal here indexes `markers`; the server's own ordinal is [3].)
  if (marks.length === 0 && !unsure) return body;

  // The spans for body[from:to]: each cut between marks gets the
  // highest-priority mark covering it.
  const range = (from: number, to: number): React.ReactNode[] => {
    const cuts = new Set<number>([from, to]);
    for (const m of marks) {
      if (m.start > from && m.start < to) cuts.add(m.start);
      if (m.end > from && m.end < to) cuts.add(m.end);
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
      if (active?.cls === "marker" && active.ordinal !== undefined) {
        out.push(
          <MarkerChip key={a} index={index} ordinal={markers[active.ordinal][3]}
            words={markers[active.ordinal][2]} text={text} />,
        );
        continue;
      }
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
  };

  if (!unsure) return range(0, body.length);
  // The passage on offer as [indistinct] is outlined as one piece; the
  // word shading inside it would otherwise cut the outline into a box
  // per word.
  const a = Math.max(0, unsure.start);
  const b = Math.min(body.length, unsure.end);
  return [
    ...range(0, a),
    <span
      key="unsure"
      style={{
        background: "var(--conf-low)",
        outline: "2px dashed var(--accent)",
        outlineOffset: 1,
        borderRadius: 3,
      }}
    >
      {range(a, b)}
    </span>,
    ...range(b, body.length),
  ];
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

  // While this paragraph is playing, the stamp turns green and shows
  // the live position ticking up as the recording plays.
  const playingHere = useReview((s) => s.playing === index);
  const playHeadSec = useReview((s) => s.playHeadSec);
  const live = playingHere && playHeadSec !== null;

  const apply = () => {
    const parsed = parseTimestamp(draft);
    if (parsed !== null) {
      void useReview.getState().setTimestamp(index, parsed);
      // Play from the corrected time so the reviewer can confirm it.
      useReview.getState().playFromTime(index, parsed);
    }
    setOpen(false);
  };

  return (
    <div className="relative pt-0.5">
      <button
        className={`rounded font-mono text-[11px] hover:underline ${
          live ? "animate-pulse" : ""
        }`}
        style={{
          color: live ? "#16a34a" : "var(--timestamp-fg)",
          fontWeight: live ? 700 : undefined,
          opacity: hidden && !live ? 0.45 : 1,
          fontStyle: amended && !live ? "italic" : undefined,
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
        {live
          ? `▶ ${formatTimestamp(playHeadSec)}`
          : hidden
            ? "[–:––]"
            : formatTimestamp(effective)}
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

type MenuAt = { x: number; y: number; index: number };

const Row = memo(function Row({
  para,
  index,
  prevSpeaker,
  showTimestamp,
  onMenu,
}: {
  para: ReviewParagraph;
  index: number;
  prevSpeaker: string | null;
  showTimestamp: boolean;
  onMenu: (at: MenuAt) => void;
}) {
  const selected = useReview((s) => s.selected === index);
  const inRange = useReview(
    (s) =>
      s.selRange !== null
      && index >= s.selRange.from
      && index <= s.selRange.to,
  );
  const editing = useReview((s) => s.editing === index);
  const qaOn = useReview((s) => s.doc?.qa != null);
  const qaMark = useReview((s) => s.doc?.qa?.rows[index]?.mark ?? "");
  const qaAsker = useReview((s) => s.doc?.qa?.rows[index]?.asker ?? null);
  const noAudio = /^\[No audio\b/i.test(para.body);
  const draft = useReview((s) => (s.editing === index ? s.editingDraft : ""));
  const showConf = useReview((s) => s.showConfidence);
  const names = useReview((s) => s.doc?.speaker_names ?? {});
  const hit = useReview((s) =>
    s.searchHit?.index === index
      ? { start: s.searchHit.start, end: s.searchHit.end }
      : null,
  );
  const unsureStart = useReview((s) =>
    s.unsureHit?.index === index ? s.unsureHit.start : -1);
  const unsureEnd = useReview((s) =>
    s.unsureHit?.index === index ? s.unsureHit.end : -1);
  const unsure = unsureStart >= 0 ? { start: unsureStart, end: unsureEnd } : null;
  const ref = useRef<HTMLDivElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (selected) ref.current?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  useEffect(() => {
    if (editing && textRef.current) {
      const el = textRef.current;
      el.focus();
      const caret = useReview.getState().editingCaret;
      if (caret !== null) el.setSelectionRange(caret, caret);
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
    if (editing || para.locked || !para.sealed) return;
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

  const onContextMenu = (e: React.MouseEvent) => {
    // Inside the edit box the system menu (spelling, cut/paste) is
    // the useful one.
    if (editing) return;
    e.preventDefault();
    const st = useReview.getState();
    if (st.editing !== null) void st.commitPendingEdit();
    // Right-clicking inside a shift-click range acts on the range;
    // anywhere else, on this paragraph alone.
    if (!inRange) st.select(index);
    onMenu({ x: e.clientX, y: e.clientY, index });
  };

  return (
    <div
      ref={ref}
      onClick={onClick}
      onContextMenu={onContextMenu}
      title={
        !para.sealed
          ? "Still being transcribed — it can still change shape, so it is not editable yet."
          : para.locked
            ? "Being re-transcribed — this section updates when the engine finishes."
            : para.suspect
              ? "This looks like repeated or low-confidence text — consider re-transcribing it (Fix a section)."
              : undefined
      }
      className="group grid cursor-default grid-cols-[130px_64px_1fr_28px] gap-3 rounded-lg px-3 py-1.5 transition-opacity"
      style={{
        background: rowBg,
        opacity: para.locked ? 0.45 : !para.sealed ? 0.6 : 1,
        boxShadow: !para.sealed
          ? "inset 3px 0 0 0 var(--fg-muted)"
          : para.locked
            ? "inset 3px 0 0 0 var(--accent)"
            : para.suspect
              ? "inset 3px 0 0 0 #d97706"
              : undefined,
      }}
    >
      {qaOn ? (
        <div className="flex flex-col items-start gap-1 pt-0.5">
          {qaMark && (
            <span className="text-sm font-bold" style={{ color: "var(--text-fg)" }}>
              {qaMark}
            </span>
          )}
          {qaAsker && (
            <span
              className="max-w-full truncate rounded-md px-1.5 py-0.5 text-[11px] font-semibold"
              style={{
                background: para.speaker ? `var(--speaker-${para.speaker})` : "var(--surface-2)",
                color: "var(--badge-fg)",
                filter: para.speaker ? "brightness(0.92)" : undefined,
              }}
              title={`${qaAsker} takes over the questions`}
            >
              {qaAsker}
            </span>
          )}
        </div>
      ) : (
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
      )}
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
            const st = useReview.getState();
            if (e.ctrlKey || e.metaKey) {
              if (e.key === "Enter") {
                e.preventDefault();
                void st.editSplitAtCaret(e.currentTarget.selectionStart);
                return;
              }
              if (e.key === "m" || e.key === "M") {
                e.preventDefault();
                void st.editJoinPrevious();
                return;
              }
              if (e.key === "ArrowUp" || e.key === "ArrowDown") {
                e.preventDefault();
                void st.editMove(e.key === "ArrowUp" ? -1 : 1);
                return;
              }
            }
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
        <div>
          <div
            className="text-sm leading-relaxed"
            style={{
              color: noAudio ? "var(--timestamp-fg)" : "var(--text-fg)",
              fontStyle: noAudio ? "italic" : undefined,
            }}
            onDoubleClick={onDoubleClick}
          >
            {renderBody(para.body, showConf ? para.conf : [], hit, unsure,
              para.markers ?? [], index)}
          </div>
          {unsure && (
            <UnsureOffer index={index} words={para.body.slice(unsure.start, unsure.end)} />
          )}
          {para.offer && <Offer index={index} offer={para.offer} />}
        </div>
      )}
      {!editing && !para.locked && para.sealed && (
        <button
          className="self-start pt-0.5 text-xs text-muted opacity-0 transition-opacity hover:!text-red-500 group-hover:opacity-60 hover:!opacity-100"
          title="Delete this paragraph (e.g. a hallucination). Undo restores it."
          onClick={(e) => {
            e.stopPropagation();
            void useReview.getState().deleteParagraph(index);
          }}
        >
          ✕
        </button>
      )}
    </div>
  );
});

/** An accepted [indistinct]. Hovering shows what the engine had
 *  transcribed there, with a button to put those words back. */
function MarkerChip({
  index,
  ordinal,
  words,
  text,
}: {
  index: number;
  ordinal: number;
  words: string;
  text: string;
}) {
  return (
    <span className="group/marker relative inline-block">
      <span
        className="cursor-help rounded px-0.5"
        style={{ background: "var(--surface-2)", color: "var(--fg-muted)" }}
      >
        {text}
      </span>
      {/* pt-1 bridges the gap, so the pointer can reach the button. */}
      <span
        className="absolute left-0 top-full z-20 hidden pt-1 group-hover/marker:block"
        onClick={(e) => e.stopPropagation()}
        onDoubleClick={(e) => e.stopPropagation()}
      >
        <span
          className="flex w-max max-w-72 items-center gap-2 rounded-lg border border-edge bg-surface px-2.5 py-1.5 text-xs shadow-lg"
          style={{ fontFamily: "system-ui, sans-serif", color: "var(--fg-muted)" }}
        >
          <span>
            Transcribed as “<span style={{ color: "var(--fg)" }}>{words}</span>”
          </span>
          <button
            className="shrink-0 rounded px-2 py-0.5 font-medium"
            style={{ background: "var(--accent)", color: "var(--accent-fg)" }}
            onClick={() =>
              void useReview.getState().restoreIndistinct(index, ordinal, words)}
          >
            Restore
          </button>
        </span>
      </span>
    </span>
  );
}

/** A passage the engine was unsure of, offered as [indistinct]. */
function UnsureOffer({ index, words }: { index: number; words: string }) {
  const st = useReview.getState();
  const btn = "rounded px-2 py-0.5 text-[11px]";
  return (
    <div
      className="mt-1.5 flex flex-wrap items-center gap-2 rounded-md px-2.5 py-1.5 text-xs"
      style={{ background: "var(--surface-2)", color: "var(--fg-muted)", fontFamily: "system-ui, sans-serif" }}
      onClick={(e) => e.stopPropagation()}
    >
      <span className="mr-auto">
        Unsure of “<span style={{ color: "var(--fg)" }}>{words}</span>”
      </span>
      <button className={`${btn} font-medium`}
        style={{ background: "var(--accent)", color: "var(--accent-fg)" }}
        onClick={() => void st.markUnsure()}>
        Mark [indistinct]
      </button>
      <button className={btn} style={{ border: "1px solid var(--border)" }}
        onClick={() => void st.keepUnsure()}>
        Keep the words
      </button>
      <button className={btn} style={{ border: "1px solid var(--border)" }}
        title="Play this paragraph (P)"
        onClick={() => st.togglePlay(index)}>
        ▶ Play
      </button>
      <button className={btn} title="Next unsure passage (U)"
        onClick={() => st.nextUnsure(1)}>
        Next ›
      </button>
    </div>
  );
}

/** The engine re-read a passage this reader had already corrected.
 *  Theirs stands; this is the alternative, offered rather than
 *  applied. */
function Offer({
  index,
  offer,
}: {
  index: number;
  offer: { text: string; source: string };
}) {
  return (
    <div
      className="mt-1.5 rounded-md border-l-2 px-2.5 py-1.5 text-xs"
      style={{
        borderColor: "var(--accent)",
        background: "var(--surface-2)",
        color: "var(--fg-muted)",
      }}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="mb-1 font-medium">
        The engine re-read this and heard:
      </div>
      <div className="mb-1.5 leading-relaxed" style={{ color: "var(--fg)" }}>
        {offer.text}
      </div>
      <div className="flex gap-2">
        <button
          className="rounded px-2 py-0.5 font-medium"
          style={{ background: "var(--accent)", color: "var(--accent-fg)" }}
          onClick={() => void useReview.getState().acceptOffer(index)}
        >
          Use this
        </button>
        <button
          className="rounded px-2 py-0.5"
          style={{ border: "1px solid var(--border)" }}
          onClick={() => void useReview.getState().dismissOffer(index)}
        >
          Keep mine
        </button>
      </div>
    </div>
  );
}

/** The document heading, as it will be saved. Click to change it;
 *  clearing it saves the document with no heading. */
function TitleLine({ title }: { title: string | null }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const done = (keep: boolean) => {
    setEditing(false);
    if (keep && draft.trim() !== (title ?? "")) {
      void useReview.getState().setTitle(draft);
    }
  };
  if (editing) {
    return (
      <input
        className="mb-6 w-full rounded border border-accent bg-transparent px-1 text-[15px] font-bold outline-none"
        style={{ color: "var(--text-fg)", fontFamily: "inherit" }}
        value={draft}
        autoFocus
        placeholder="No heading"
        onFocus={(e) => e.currentTarget.select()}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => done(true)}
        onKeyDown={(e) => {
          if (e.key !== "Enter" && e.key !== "Escape") return;
          // Handled here: the box is gone before the key reaches the
          // pane's shortcuts, which would otherwise read this Enter as
          // "edit the selected paragraph".
          e.preventDefault();
          done(e.key === "Enter");
        }}
      />
    );
  }
  return (
    <h1
      className="mb-6 cursor-text rounded px-1 text-[15px] font-bold hover:bg-surface-2"
      style={{
        color: title ? "var(--text-fg)" : "var(--timestamp-fg)",
        fontStyle: title ? undefined : "italic",
        fontWeight: title ? undefined : 400,
      }}
      title="Click to change the heading"
      onClick={() => {
        setDraft(title ?? "");
        setEditing(true);
      }}
    >
      {title || "Add a heading"}
    </h1>
  );
}

/** Right-click menu on a paragraph, or on a shift-click range. */
function RowMenu({ at, onClose }: { at: MenuAt; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ left: at.x, top: at.y });
  const paragraphs = useReview((s) => s.doc?.paragraphs ?? []);
  const selRange = useReview((s) => s.selRange);
  const running = useReview((s) => s.retrans.running);
  const audioReady = useReview((s) => s.doc?.audio.state === "ready");

  // Keep the menu on screen near the window's right and bottom edges.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const { width, height } = el.getBoundingClientRect();
    setPos({
      left: Math.max(4, Math.min(at.x, window.innerWidth - width - 4)),
      top: Math.max(4, Math.min(at.y, window.innerHeight - height - 4)),
    });
  }, [at]);

  useEffect(() => {
    const away = (e: Event) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("mousedown", away, true);
    document.addEventListener("scroll", onClose, true);
    document.addEventListener("keydown", key, true);
    window.addEventListener("blur", onClose);
    window.addEventListener("resize", onClose);
    return () => {
      document.removeEventListener("mousedown", away, true);
      document.removeEventListener("scroll", onClose, true);
      document.removeEventListener("keydown", key, true);
      window.removeEventListener("blur", onClose);
      window.removeEventListener("resize", onClose);
    };
  }, [onClose]);

  const range = selRange ?? { from: at.index, to: at.index };
  const span = paragraphs.slice(range.from, range.to + 1);
  const count = span.length;
  const single = selRange === null;
  const para = paragraphs[at.index];
  if (!para || count === 0) return null;

  const busy = span.find((p) => !p.sealed || p.locked);
  const why = running
    ? "Another section is being re-transcribed."
    : !audioReady
      ? "Needs the source recording — use Locate audio… on the Playback card."
      : busy
        ? busy.locked
          ? "Part of this is already being re-transcribed."
          : "Part of this is still being transcribed."
        : undefined;
  const editableHere = para.sealed && !para.locked;
  const prev = paragraphs[at.index - 1];

  const act = (fn: () => void) => () => {
    onClose();
    fn();
  };
  const st = useReview.getState();

  return (
    <div
      ref={ref}
      role="menu"
      className="fixed z-50 min-w-56 rounded-lg border border-edge bg-surface py-1 text-xs shadow-lg"
      style={{ left: pos.left, top: pos.top }}
      onContextMenu={(e) => e.preventDefault()}
    >
      <MenuItem
        label={
          count === 1
            ? "Re-transcribe this paragraph"
            : `Re-transcribe ${count} paragraphs`
        }
        hint={(st.retransModel ?? st.doc?.retrans_default) || undefined}
        disabled={!!why}
        title={why ?? "Uses the model and options in Fix a section."}
        onClick={act(
          () => void st.retranscribe(st.retransModel, st.retransCondition),
        )}
      />
      {single && (
        <>
          <div className="my-1 border-t border-edge" />
          <MenuItem
            label="Edit"
            hint="Enter"
            disabled={!editableHere}
            onClick={act(() => st.startEdit(at.index))}
          />
          {para.unsure.length > 0 && (
            <MenuItem
              label={`Review ${para.unsure.length} unsure passage${para.unsure.length === 1 ? "" : "s"}`}
              hint="U"
              onClick={act(() => {
                const [a, b] = para.unsure[0];
                useReview.setState({
                  unsureHit: { index: at.index, start: a, end: b, text: para.body.slice(a, b) },
                });
              })}
            />
          )}
          <MenuItem
            label="Join to previous"
            hint="M"
            disabled={
              !editableHere || !prev || !prev.sealed || prev.locked
            }
            onClick={act(() => void st.merge(at.index))}
          />
          <MenuItem
            label="Delete paragraph"
            hint="⌫"
            disabled={!editableHere || paragraphs.length <= 1}
            onClick={act(() => void st.deleteParagraph(at.index))}
          />
        </>
      )}
    </div>
  );
}

function MenuItem({
  label,
  hint,
  disabled,
  title,
  onClick,
}: {
  label: string;
  hint?: string;
  disabled?: boolean;
  title?: string;
  onClick: () => void;
}) {
  return (
    <button
      role="menuitem"
      className="flex w-full items-baseline justify-between gap-6 px-3 py-1.5 text-left hover:bg-surface-2 disabled:opacity-40 disabled:hover:bg-transparent"
      style={{ fontFamily: "system-ui, sans-serif" }}
      disabled={disabled}
      title={title}
      onClick={onClick}
    >
      <span>{label}</span>
      {hint && <span className="text-[11px] text-muted">{hint}</span>}
    </button>
  );
}

export default function TranscriptDoc() {
  const doc = useReview((s) => s.doc);
  const [menu, setMenu] = useState<MenuAt | null>(null);
  const closeMenu = useCallback(() => setMenu(null), []);
  if (!doc) return null;
  // A sheet, laid out the way writers.py lays out the .docx: A4-ish
  // measure, the title in bold above the transcript, speaker labels on
  // their own line above the block they introduce, timestamps hanging
  // in the left margin, and the disclaimer at the foot. What you read
  // here is what the file will look like.
  return (
    <div className="px-6 py-8">
      <div
        className="mx-auto max-w-[52rem] rounded-xl border border-edge px-10 py-9 shadow-sm"
        style={{ background: "var(--text-bg)", fontFamily: DOC_FONT }}
      >
        <TitleLine title={doc.title} />
        {doc.paragraphs.map((p, i) => (
          <Row
            key={p.id}
            para={p}
            index={i}
            prevSpeaker={i > 0 ? doc.paragraphs[i - 1].speaker : null}
            showTimestamp={doc.show_timestamp}
            onMenu={setMenu}
          />
        ))}
        {menu && <RowMenu at={menu} onClose={closeMenu} />}
        {doc.live && (
          <div
            className="mt-4 flex items-center gap-2 text-xs"
            style={{ color: "var(--timestamp-fg)" }}
          >
            <span
              className="inline-block h-1.5 w-1.5 animate-pulse rounded-full"
              style={{ background: "var(--accent)" }}
              aria-hidden
            />
            still transcribing…
          </div>
        )}
        {!doc.live && doc.paragraphs.length > 0 && (
          <p
            className="mt-8 border-t border-edge pt-4 text-[11px] italic"
            style={{ color: "var(--timestamp-fg)" }}
          >
            {doc.verified_by
              ? `Transcribed using Transcribr. This transcript has been verified by ${doc.verified_by}.`
              : "Transcribed using Transcribr. If this text has not been deleted, the accuracy of this transcript may not have been checked by a human."}
          </p>
        )}
      </div>
    </div>
  );
}

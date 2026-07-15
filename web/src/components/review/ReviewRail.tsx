import { useEffect, useRef, useState } from "react";
import { useReview } from "../../state/reviewStore";
import { CheckField, inputCls } from "../fields";

// Right rail: speakers panel, find & replace, confidence toggle.
// (The playback card joins in Phase 4.)

function SpeakerRow({ slot }: { slot: string }) {
  const serverName = useReview((s) => s.doc?.speaker_names[slot] ?? "");
  const [local, setLocal] = useState(serverName);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const focused = useRef(false);

  useEffect(() => {
    if (!focused.current) setLocal(serverName);
  }, [serverName]);

  return (
    <div className="flex items-center gap-2">
      <span
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-xs font-bold"
        style={{ background: `var(--speaker-${slot})`, color: "var(--badge-fg)" }}
      >
        {slot}
      </span>
      <input
        className={`${inputCls} w-full py-1.5`}
        value={local}
        onFocus={() => (focused.current = true)}
        onBlur={() => (focused.current = false)}
        onChange={(e) => {
          setLocal(e.target.value);
          clearTimeout(timer.current);
          timer.current = setTimeout(() => {
            void useReview.getState().setSpeakerName(slot, e.target.value);
          }, 300);
        }}
      />
    </div>
  );
}

function SpeakersPanel() {
  const visible = useReview((s) => s.doc?.visible_speakers ?? 4);
  const slots = Array.from({ length: visible }, (_, i) => String(i + 1));
  return (
    <section className="rounded-xl border border-edge bg-surface p-4">
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">
        Speakers
      </h2>
      <div className="flex flex-col gap-2">
        {slots.map((slot) => (
          <SpeakerRow key={slot} slot={slot} />
        ))}
      </div>
      {visible < 9 && (
        <button
          className="mt-3 text-xs font-medium text-accent hover:underline"
          onClick={() => void useReview.getState().addSpeaker()}
        >
          + Add speaker
        </button>
      )}
      <p className="mt-3 text-[11px] leading-relaxed text-muted">
        Select a paragraph and press 1–9 to label it, 0 to clear.
      </p>
    </section>
  );
}

function FindReplace() {
  const findTerm = useReview((s) => s.findTerm);
  const replaceTerm = useReview((s) => s.replaceTerm);
  const matchCase = useReview((s) => s.matchCase);
  const status = useReview((s) => s.findStatus);
  const btn =
    "rounded-lg border border-edge px-2.5 py-1.5 text-xs font-medium hover:bg-surface-2";

  return (
    <section className="rounded-xl border border-edge bg-surface p-4">
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">
        Find &amp; replace
      </h2>
      <input
        id="review-find"
        className={`${inputCls} mb-2 w-full py-1.5`}
        placeholder="Find (⌘F)"
        value={findTerm}
        onChange={(e) => useReview.getState().setFindTerm(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            useReview.getState().findNext();
          }
        }}
      />
      <input
        className={`${inputCls} mb-3 w-full py-1.5`}
        placeholder="Replace with"
        value={replaceTerm}
        onChange={(e) => useReview.getState().setReplaceTerm(e.target.value)}
      />
      <div className="flex items-center gap-2">
        <button className={btn} onClick={() => useReview.getState().findNext()}>
          Find next
        </button>
        <button className={btn} onClick={() => void useReview.getState().replaceAll()}>
          Replace all
        </button>
        <span className="ml-auto text-[11px] text-muted">{status}</span>
      </div>
      <div className="mt-2.5">
        <CheckField
          label="Match case"
          checked={matchCase}
          onChange={(v) => useReview.getState().setMatchCase(v)}
        />
      </div>
    </section>
  );
}

export default function ReviewRail() {
  const hasConf = useReview((s) => s.doc?.has_word_conf ?? false);
  const showConf = useReview((s) => s.showConfidence);
  return (
    <aside className="flex w-72 shrink-0 flex-col gap-4 overflow-y-auto">
      <SpeakersPanel />
      <FindReplace />
      {hasConf && (
        <section className="rounded-xl border border-edge bg-surface p-4">
          <CheckField
            label="Shade low-confidence words"
            checked={showConf}
            onChange={(v) => useReview.getState().setShowConfidence(v)}
          />
        </section>
      )}
      <section className="rounded-xl border border-dashed border-edge p-4 text-[11px] text-muted">
        Audio playback (P key) arrives in Phase 4.
      </section>
    </aside>
  );
}

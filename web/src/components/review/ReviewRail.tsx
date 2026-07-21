import { useEffect, useRef, useState } from "react";
import { useReview } from "../../state/reviewStore";
import { useApp } from "../../state/store";
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

function PlaybackCard() {
  const audio = useReview((s) => s.doc?.audio);
  const playing = useReview((s) => s.playing);
  const playingThrough = useReview((s) => s.playingThrough);
  const selected = useReview((s) => s.selected);
  if (!audio) return null;

  const paraActive = playing !== null && !playingThrough;
  const throughActive = playing !== null && playingThrough;
  const label =
    audio.state === "ready"
      ? paraActive
        ? "■ Stop"
        : "▶ Play paragraph"
      : audio.state === "probing"
        ? "Checking audio…"
        : audio.state === "extracting"
          ? "Preparing audio…"
          : null;

  return (
    <section className="rounded-xl border border-edge bg-surface p-4">
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">
        Playback
      </h2>
      {label === null ? (
        <>
          <p className="text-[11px] leading-relaxed text-muted">
            No audio available for this transcript
            {audio.error ? ` — ${audio.error}` : "."}
          </p>
          <button
            className="mt-2 w-full rounded-lg border border-edge px-3 py-2 text-sm font-medium hover:bg-surface-2"
            onClick={() => void useReview.getState().locateAudio()}
          >
            Locate audio…
          </button>
          <p className="mt-2 text-[11px] text-muted">
            Point at the original recording if it has moved.
          </p>
        </>
      ) : (
        <>
          <button
            className="w-full rounded-lg border border-edge px-3 py-2 text-sm font-medium hover:bg-surface-2 disabled:opacity-40"
            disabled={audio.state !== "ready"}
            onClick={() =>
              useReview
                .getState()
                .togglePlay(paraActive ? playing! : selected)
            }
          >
            {label}
          </button>
          <button
            className="mt-2 w-full rounded-lg border border-edge px-3 py-2 text-sm font-medium hover:bg-surface-2 disabled:opacity-40"
            disabled={audio.state !== "ready"}
            onClick={() =>
              useReview
                .getState()
                .togglePlay(throughActive ? playing! : selected, true)
            }
          >
            {throughActive ? "■ Stop playing" : "▶▶ Play from here"}
          </button>
          <p className="mt-2 text-[11px] text-muted">
            P plays just the selected paragraph (press again to stop);
            ⌘P plays on from it.
          </p>
        </>
      )}
    </section>
  );
}

function VerifyCard() {
  const verifyName = useReview((s) => s.verifyName);
  return (
    <section className="rounded-xl border border-edge bg-surface p-4">
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">
        Verify transcript
      </h2>
      <input
        className={`${inputCls} w-full py-1.5`}
        placeholder="Your name"
        value={verifyName}
        onChange={(e) => useReview.getState().setVerifyName(e.target.value)}
      />
      <p className="mt-2 text-[11px] leading-relaxed text-muted">
        {verifyName.trim()
          ? `Saved documents will state: "This transcript has been verified by ${verifyName.trim()}."`
          : "Enter your name to certify you have checked this transcript. " +
            "Until then, saved documents carry a warning that accuracy " +
            "may not have been checked by a human."}
      </p>
    </section>
  );
}

function TimestampsCard() {
  const doc = useReview((s) => s.doc);
  const saveShowTimestamp = useReview((s) => s.saveShowTimestamp);
  const hasConf = useReview((s) => s.doc?.has_word_conf ?? false);
  const showConf = useReview((s) => s.showConfidence);
  if (!doc) return null;
  const timestamps = saveShowTimestamp ?? doc.show_timestamp;

  return (
    <section className="rounded-xl border border-edge bg-surface p-4">
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">
        Timestamps / uncertain words
      </h2>
      <div className="flex flex-col gap-2.5">
        <CheckField
          label="Include timestamps in the saved file"
          checked={timestamps}
          onChange={(v) => useReview.getState().setSaveShowTimestamp(v)}
        />
        {hasConf && (
          <CheckField
            label="Shade low-confidence words"
            checked={showConf}
            onChange={(v) => useReview.getState().setShowConfidence(v)}
          />
        )}
      </div>
    </section>
  );
}

function FixSectionCard() {
  const selected = useReview((s) => s.selected);
  const selRange = useReview((s) => s.selRange);
  const retrans = useReview((s) => s.retrans);
  const audioReady = useReview((s) => s.doc?.audio.state === "ready");
  const meta = useApp((s) => s.meta);
  const settings = useApp((s) => s.settings);
  const [model, setModel] = useState("");
  const [condition, setCondition] = useState(false);
  const [showLog, setShowLog] = useState(false);
  const logRef = useRef<HTMLPreElement>(null);

  const hasActivity = retrans.running || retrans.log.length > 0;
  // Reveal the output box automatically once a run starts.
  useEffect(() => {
    if (retrans.running) setShowLog(true);
  }, [retrans.running]);
  // Keep the newest engine output in view.
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [retrans.log]);

  const range = selRange ?? { from: selected, to: selected };
  const paragraphs = useReview((s) => s.doc?.paragraphs ?? []);
  const count = Math.min(range.to, paragraphs.length - 1) - range.from + 1;
  const suspectCount = paragraphs.filter((p) => p.suspect).length;
  const english = settings?.language === "English";
  const tiers = meta?.model_tiers ?? [];
  const btn =
    "rounded-lg border border-edge px-2.5 py-1.5 text-xs font-medium hover:bg-surface-2 disabled:opacity-40";

  return (
    <section className="rounded-xl border border-edge bg-surface p-4">
      <h2 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
        Fix a section
      </h2>
      <p className="mb-3 text-[11px] leading-relaxed text-muted">
        Re-run part of the recording through the engine — for repeated or
        invented text. Shift-click extends the selection; the selected
        paragraphs are replaced, and undo reverses it.
      </p>

      {suspectCount > 0 && (
        <div className="mb-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-2.5">
          <p className="text-[11px] leading-relaxed text-amber-700 dark:text-amber-400">
            ⚠ {suspectCount} paragraph{suspectCount === 1 ? "" : "s"}{" "}
            {suspectCount === 1 ? "looks" : "look"} like repeated or
            hallucinated text.
          </p>
          <button
            className="mt-1.5 rounded border border-amber-500/50 px-2 py-0.5 text-[11px] font-medium text-amber-700 hover:bg-amber-500/15 dark:text-amber-400"
            onClick={() => useReview.getState().jumpNextSuspect()}
          >
            Jump to next
          </button>
        </div>
      )}
      <label className="mb-1 block text-[11px] font-medium text-muted">
        Model for the re-run
      </label>
      <select
        className={`${inputCls} mb-2 w-full py-1.5 text-xs`}
        value={model}
        disabled={retrans.running}
        onChange={(e) => setModel(e.target.value)}
      >
        <option value="">Same as your current setting</option>
        {tiers.map((t) => {
          const m = english ? t.model_en : t.model;
          return (
            <option key={t.id} value={m}>
              {t.label} ({m})
            </option>
          );
        })}
      </select>
      <CheckField
        label="Condition on previous text"
        checked={condition}
        onChange={setCondition}
        note="Leave off for hallucinated sections — carrying context in is usually what caused them."
      />
      <div className="mt-3 flex items-center gap-2">
        {retrans.running ? (
          <button
            className={btn}
            onClick={() => void useReview.getState().cancelRetranscribe()}
          >
            Cancel
          </button>
        ) : (
          <button
            className={`${btn} border-accent text-accent`}
            disabled={!audioReady || count === 0}
            onClick={() =>
              void useReview.getState().retranscribe(model, condition)
            }
          >
            Re-transcribe {count} paragraph{count === 1 ? "" : "s"}
          </button>
        )}
      </div>

      {hasActivity && (
        <div className="mt-3">
          <div className="flex items-baseline justify-between gap-2">
            <span className="truncate text-[11px] text-muted">
              {retrans.message || "Working…"}
            </span>
            <span className="shrink-0 text-[11px] tabular-nums text-muted">
              {retrans.running && retrans.indeterminate
                ? ""
                : `${Math.round(retrans.pct)}%`}
            </span>
          </div>
          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-2">
            {retrans.running && retrans.indeterminate ? (
              <div className="h-full w-1/3 animate-progress-indeterminate rounded-full bg-accent" />
            ) : (
              <div
                className="h-full rounded-full bg-accent transition-[width] duration-300"
                style={{ width: `${retrans.pct}%` }}
              />
            )}
          </div>
          <button
            className="mt-2 text-[11px] text-muted hover:text-fg"
            onClick={() => setShowLog((v) => !v)}
          >
            {showLog ? "Hide output ▾" : "Show output ▸"}
          </button>
          {showLog && (
            <pre
              ref={logRef}
              className="mt-1 h-32 overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-surface-2 p-2 font-mono text-[10px] leading-relaxed text-muted"
            >
              {retrans.log || "No output yet."}
            </pre>
          )}
        </div>
      )}

      {!hasActivity && !audioReady && (
        <p className="mt-2 text-[11px] leading-relaxed text-muted">
          Needs the source recording — use Locate audio… on the Playback
          card.
        </p>
      )}
    </section>
  );
}

export default function ReviewRail() {
  return (
    <aside className="flex w-72 shrink-0 flex-col gap-4 overflow-y-auto">
      <SpeakersPanel />
      <PlaybackCard />
      <FixSectionCard />
      <TimestampsCard />
      <FindReplace />
      <VerifyCard />
    </aside>
  );
}

import { useEffect, useRef, useState } from "react";
import { useReview } from "../../state/reviewStore";
import { api } from "../../api/client";
import { useApp } from "../../state/store";
import { CheckField, SelectField, inputCls } from "../fields";
import { TEMPLATE_OPTIONS } from "../../views/SettingsView";

// The review pane's tools rail. Every card but the layout switch folds
// to its title, and remembers whether it was open.

function readOpen(id: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(`transcribr.rail.${id}`);
    return v === null ? fallback : v === "1";
  } catch {
    return fallback;
  }
}

function writeOpen(id: string, open: boolean): void {
  try {
    localStorage.setItem(`transcribr.rail.${id}`, open ? "1" : "0");
  } catch {
    /* a convenience only */
  }
}

/** A card that folds to its title. `force` holds it open (a run in
 *  progress) without changing what is remembered. */
function RailCard({
  id,
  title,
  defaultOpen = false,
  force = false,
  cardRef,
  children,
}: {
  id: string;
  title: string;
  defaultOpen?: boolean;
  force?: boolean;
  cardRef?: React.Ref<HTMLElement>;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(() => readOpen(id, defaultOpen));
  const shown = open || force;
  return (
    <section ref={cardRef} className="rounded-xl border border-edge bg-surface">
      <button
        className="flex w-full items-center justify-between px-4 py-3 text-xs font-semibold uppercase tracking-wide text-muted hover:text-fg"
        aria-expanded={shown}
        onClick={() => {
          setOpen(!shown);
          writeOpen(id, !shown);
        }}
      >
        {title}
        <span aria-hidden className="text-[10px]">{shown ? "▾" : "▸"}</span>
      </button>
      {shown && <div className="px-4 pb-4">{children}</div>}
    </section>
  );
}

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
      <AskToggle slot={slot} />
    </div>
  );
}

/** In Q&A mode: whether this speaker's paragraphs are questions or
 *  answers. */
function AskToggle({ slot }: { slot: string }) {
  const qa = useReview((s) => s.doc?.qa ?? null);
  if (!qa) return null;
  const asks = qa.askers.includes(slot);
  return (
    <button
      className="w-8 shrink-0 rounded-md border border-edge py-1 text-xs font-bold hover:bg-surface-2"
      style={asks ? { background: "var(--accent)", color: "var(--accent-fg)", borderColor: "var(--accent)" } : undefined}
      title={asks
        ? "Asks the questions (Q) — click to make this speaker an answerer"
        : "Answers (A) — click to make this speaker a questioner"}
      onClick={() => {
        const next = asks
          ? qa.askers.filter((a) => a !== slot)
          : [...qa.askers, slot];
        void useReview.getState().setAskers(next);
      }}
    >
      {asks ? "Q" : "A"}
    </button>
  );
}

/** Normal transcript, or numbered questions and answers like an ERISP.
 *  What is shown here is what Save writes. */
function LayoutCard() {
  const layout = useReview((s) => s.doc?.layout ?? "plain");
  const qa = useReview((s) => s.doc?.qa ?? null);
  const seg = (on: boolean) =>
    `flex-1 rounded-md px-3 py-1.5 text-xs font-medium ${
      on ? "bg-surface shadow-sm" : "text-muted hover:text-fg"
    }`;
  return (
    <section className="rounded-xl border border-edge bg-surface p-4">
      <div className="flex rounded-lg bg-surface-2 p-0.5">
        <button className={seg(layout === "plain")}
          onClick={() => void useReview.getState().setLayout("plain")}>
          Transcript
        </button>
        <button className={seg(layout === "qa")}
          onClick={() => void useReview.getState().setLayout("qa")}>
          Q &amp; A
        </button>
      </div>
      {qa && (
        <div className="mt-3 text-[11px] leading-relaxed text-muted">
          <p>{qa.note}</p>
          <p className="mt-1.5">
            Set who asks with the Q / A buttons beside each speaker.
            {!qa.auto && (
              <>
                {" "}
                <button className="font-medium text-accent hover:underline"
                  onClick={() => void useReview.getState().setAskers(null)}>
                  Use the suggestion
                </button>
              </>
            )}
          </p>
        </div>
      )}
    </section>
  );
}

function SpeakersPanel() {
  const visible = useReview((s) => s.doc?.visible_speakers ?? 4);
  const slots = Array.from({ length: visible }, (_, i) => String(i + 1));
  return (
    <RailCard id="speakers" title="Speakers" defaultOpen>
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
    </RailCard>
  );
}

/** Passages the engine was unsure of, offered as [indistinct] one at a
 *  time. Nothing changes unless the reviewer accepts. */
function UnsureCard() {
  const count = useReview((s) =>
    (s.doc?.paragraphs ?? []).reduce((n, p) => n + (p.unsure?.length ?? 0), 0));
  const hasConf = useReview((s) => s.doc?.has_word_conf ?? false);
  const reviewing = useReview((s) => s.unsureHit !== null);
  const offer = useApp((s) => s.settings?.offer_indistinct ?? true);
  if (!hasConf) return null;
  const btn =
    "rounded-lg border border-edge px-2.5 py-1.5 text-xs font-medium hover:bg-surface-2 disabled:opacity-40";
  return (
    <RailCard id="unsure" title="Unsure words">
      <CheckField
        label="Offer [indistinct] for unclear words"
        checked={offer}
        onChange={async (v) => {
          useReview.setState({ unsureHit: null });
          useApp.getState().updateSettings({ offer_indistinct: v });
          // Saved now rather than after the usual pause, so the
          // transcript refetched next already reflects it.
          await api.put("/api/settings", useApp.getState().settings).catch(() => {});
          await useReview.getState().refetch();
        }}
      />
      {offer && (
      <>
      <p className="mb-3 mt-2 text-[11px] leading-relaxed text-muted">
        {count === 0
          ? "Nothing left that the engine was unsure of."
          : `${count} passage${count === 1 ? "" : "s"} the engine was unsure of. ` +
            "Mark each [indistinct], or keep the words."}
      </p>
      <div className="flex gap-2">
        <button className={btn} disabled={count === 0}
          onClick={() => useReview.getState().nextUnsure(-1)}>
          ‹ Previous
        </button>
        <button className={`${btn} border-accent text-accent`} disabled={count === 0}
          onClick={() => useReview.getState().nextUnsure(1)}>
          {reviewing ? "Next ›" : "Start"}
        </button>
        {reviewing && (
          <button className={btn}
            onClick={() => useReview.setState({ unsureHit: null })}>
            Stop
          </button>
        )}
      </div>
      </>
      )}
      <p className="mt-2 text-[11px] leading-relaxed text-muted">
        Hover over an [indistinct] to see what was transcribed there, and
        to put it back.
      </p>
    </RailCard>
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
    <RailCard id="find" title="Find & replace">
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
    </RailCard>
  );
}

function NamesCard() {
  const terms = useReview((s) => s.terms);
  const loading = useReview((s) => s.termsLoading);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [note, setNote] = useState("");

  const show = () => {
    setOpen(true);
    if (terms === null) void useReview.getState().loadTerms();
  };

  const apply = async (variants: string[]) => {
    const to = draft.trim();
    setEditing(null);
    if (!to) return;
    const n = await useReview.getState().applyTerm(variants, to);
    setNote(
      n === 0
        ? "Nothing to change."
        : `Corrected ${n} ${n === 1 ? "occurrence" : "occurrences"}.`,
    );
  };

  return (
    <RailCard id="names" title="Names & terms">

      {!open ? (
        <>
          <button
            className="w-full rounded-lg border border-edge px-3 py-2 text-sm font-medium hover:bg-surface-2"
            onClick={show}
          >
            Check names and terms
          </button>
          <p className="mt-2 text-[11px] leading-relaxed text-muted">
            Correct a misspelt name everywhere at once.
          </p>
        </>
      ) : loading ? (
        <p className="text-[11px] text-muted">Reading the transcript…</p>
      ) : !terms || terms.length === 0 ? (
        <p className="text-[11px] text-muted">
          No names or unusual terms found.
        </p>
      ) : (
        <>
          <ul className="flex max-h-80 flex-col gap-1.5 overflow-y-auto">
            {terms.map((g) => {
              const variants = g.variants.map((v) => v.term);
              const spellings = g.variants.length > 1;
              return (
                <li key={g.term} className="text-sm">
                  {editing === g.term ? (
                    <div className="flex items-center gap-1.5">
                      <input
                        autoFocus
                        className={`${inputCls} w-full py-1`}
                        value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") void apply(variants);
                          if (e.key === "Escape") setEditing(null);
                        }}
                      />
                      <button
                        className="shrink-0 rounded-lg border border-edge px-2 py-1 text-xs hover:bg-surface-2"
                        onClick={() => void apply(variants)}
                      >
                        Apply
                      </button>
                    </div>
                  ) : null}
                  {editing === g.term && spellings ? (
                    <div className="px-1.5 pt-1 text-[11px] text-muted">
                      Rewrites {variants.join(", ")} — check these are the
                      same name before applying.
                    </div>
                  ) : (
                    <button
                      className="flex w-full items-baseline gap-2 rounded-lg px-1.5 py-1 text-left hover:bg-surface-2"
                      onClick={() => {
                        setEditing(g.term);
                        setDraft(g.term);
                        setNote("");
                      }}
                    >
                      <span className="truncate font-medium">{g.term}</span>
                      <span className="ml-auto shrink-0 text-[11px] text-muted">
                        {g.count}
                      </span>
                    </button>
                  )}
                  {spellings && editing !== g.term && (
                    <div className="px-1.5 pb-0.5 text-[11px] text-muted">
                      also {g.variants
                        .filter((v) => v.term !== g.term)
                        .map((v) => v.term)
                        .join(", ")}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          <div className="mt-2 flex items-center gap-2">
            <button
              className="rounded-lg border border-edge px-2.5 py-1.5 text-xs font-medium hover:bg-surface-2"
              onClick={() => void useReview.getState().loadTerms()}
            >
              Refresh
            </button>
            <span className="text-[11px] text-muted">{note}</span>
          </div>
        </>
      )}
    </RailCard>
  );
}

function PlaybackCard() {
  const audio = useReview((s) => s.doc?.audio);
  const playing = useReview((s) => s.playing);
  const playingThrough = useReview((s) => s.playingThrough);
  const selected = useReview((s) => s.selected);
  const halfSpeed = useReview((s) => s.halfSpeed);
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
    <RailCard id="playback" title="Playback">
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
          <label className="mt-3 flex cursor-pointer items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="h-4 w-4"
              checked={halfSpeed}
              onChange={(e) =>
                useReview.getState().setHalfSpeed(e.target.checked)
              }
            />
            Half speed (0.5×)
          </label>
        </>
      )}
    </RailCard>
  );
}

function VerifyCard() {
  const verifyName = useReview((s) => s.verifyName);
  return (
    <RailCard id="verify" title="Verify">
      <input
        className={`${inputCls} w-full py-1.5`}
        placeholder="Your name"
        value={verifyName}
        onChange={(e) => useReview.getState().setVerifyName(e.target.value)}
      />
      <p className="mt-2 text-[11px] leading-relaxed text-muted">
        {verifyName.trim()
          ? `Saved documents will state: "This transcript has been verified by ${verifyName.trim()}."`
          : "Add your name once you have checked the transcript. Until " +
            "then it is saved with a not-checked warning."}
      </p>
    </RailCard>
  );
}

function TimestampsCard() {
  const doc = useReview((s) => s.doc);
  const saveShowTimestamp = useReview((s) => s.saveShowTimestamp);
  const saveColourSpeakers = useReview((s) => s.saveColourSpeakers);
  const hasConf = useReview((s) => s.doc?.has_word_conf ?? false);
  const showConf = useReview((s) => s.showConfidence);
  const settingsFormat = useApp((s) => s.settings?.output_format);
  const template = useApp((s) => s.settings?.doc_template ?? "plain");
  if (!doc) return null;
  const timestamps = saveShowTimestamp ?? doc.show_timestamp;
  const colour = saveColourSpeakers ?? doc.colour_speakers;
  // Saving follows the Settings page's format when it has one.
  const format = settingsFormat ?? doc.output_format;
  const canColour = format === "docx" || format === "pdf";

  return (
    <RailCard id="saved" title="Saved file">
      <div className="flex flex-col gap-2.5">
        <SelectField
          label="Word layout"
          value={template}
          options={TEMPLATE_OPTIONS}
          onChange={(v) =>
            void useApp.getState().updateSettings({ doc_template: v as typeof template })}
          note={format === "docx" ? undefined : "Applies to Word files; this one saves as " + format + "."}
        />
        <CheckField
          label="Include timestamps in the saved file"
          checked={timestamps}
          onChange={(v) => useReview.getState().setSaveShowTimestamp(v)}
        />
        <CheckField
          label="Colour each speaker's paragraphs"
          checked={colour && canColour}
          disabled={!canColour}
          note={
            canColour ? undefined : "Word and PDF only."
          }
          onChange={(v) => useReview.getState().setSaveColourSpeakers(v)}
        />
        {hasConf && (
          <CheckField
            label="Shade low-confidence words"
            checked={showConf}
            onChange={(v) => useReview.getState().setShowConfidence(v)}
          />
        )}
      </div>
    </RailCard>
  );
}

function FixSectionCard() {
  const selected = useReview((s) => s.selected);
  const selRange = useReview((s) => s.selRange);
  const retrans = useReview((s) => s.retrans);
  const audioReady = useReview((s) => s.doc?.audio.state === "ready");
  const meta = useApp((s) => s.meta);
  const settings = useApp((s) => s.settings);
  const model = useReview((s) => s.retransModel);
  const condition = useReview((s) => s.retransCondition);
  const { setRetransModel: setModel, setRetransCondition: setCondition } =
    useReview.getState();
  const [showLog, setShowLog] = useState(false);
  const logRef = useRef<HTMLPreElement>(null);

  const hasActivity = retrans.running || retrans.log.length > 0;
  const cardRef = useRef<HTMLElement>(null);
  // Reveal the output box automatically once a run starts, and bring
  // the card into view - the run may have been started from the
  // paragraph menu, with this card scrolled away.
  useEffect(() => {
    if (retrans.running) {
      setShowLog(true);
      cardRef.current?.scrollIntoView({ block: "nearest" });
    }
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
  // Default to the other engine family from the one that made the
  // transcript (the server's suggestion) until a choice is made here.
  const suggested = useReview((s) => s.doc?.retrans_default ?? "");
  const runModel = useReview((s) => s.doc?.run_model ?? null);
  const tierModels = tiers.map((t) => (english ? t.model_en : t.model));
  const choices = [
    ...tierModels,
    ...[suggested, runModel].filter(
      (m): m is string => !!m && !tierModels.includes(m),
    ),
  ];
  const labelFor = (m: string) => {
    const t = tiers.find((t) => (english ? t.model_en : t.model) === m);
    return t ? `${t.label} (${m})` : m;
  };
  const btn =
    "rounded-lg border border-edge px-2.5 py-1.5 text-xs font-medium hover:bg-surface-2 disabled:opacity-40";

  return (
    <RailCard id="retranscribe" title="Re-transcribe" force={retrans.running}
      cardRef={cardRef}>
      <p className="mb-3 text-[11px] leading-relaxed text-muted">
        For repeated or invented text. Right-click a paragraph, or
        shift-click to take in several. Undo reverses it.
      </p>

      {suspectCount > 0 && (
        <div className="mb-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-2.5">
          <p className="text-[11px] leading-relaxed text-amber-700 dark:text-amber-400">
            ⚠ {suspectCount} paragraph{suspectCount === 1 ? "" : "s"}{" "}
            {suspectCount === 1 ? "looks" : "look"} like repeated or
            low-confidence text.
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
        value={model ?? suggested}
        disabled={retrans.running}
        onChange={(e) => setModel(e.target.value)}
      >
        <option value="">Same as your current setting</option>
        {choices.map((m) => (
          <option key={m} value={m}>
            {labelFor(m)}
            {m === runModel ? " — made this transcript" : ""}
          </option>
        ))}
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
    </RailCard>
  );
}

export default function ReviewRail() {
  return (
    <aside className="flex w-72 shrink-0 flex-col gap-4 overflow-y-auto">
      <LayoutCard />
      <SpeakersPanel />
      <PlaybackCard />
      <FixSectionCard />
      <UnsureCard />
      <NamesCard />
      <FindReplace />
      <TimestampsCard />
      <VerifyCard />
    </aside>
  );
}

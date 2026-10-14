import { api } from "../api/client";
import { useApp } from "../state/store";
import { Card, CheckField, NumberField, SelectField } from "../components/fields";
import { useUpdate } from "../state/updateStore";
import ModelsView from "./ModelsView";
import type { ThemeSetting } from "../api/types";

// The advanced-settings page: everything that shouldn't crowd the
// Transcribe page but still deserves a home. All values persist to
// settings.json like every other option.

export const TEMPLATE_OPTIONS = [
  { value: "plain", label: "Plain" },
  { value: "court", label: "Court — numbered lines, double spaced" },
  { value: "erisp", label: "ERISP — record of interview" },
];

export default function SettingsView() {
  const meta = useApp((s) => s.meta)!;
  const settings = useApp((s) => s.settings);
  if (!settings) return null;
  const update = useApp.getState().updateSettings;

  const onLanguageChange = (v: string) => {
    // Keep the chosen model tier when the language flips between
    // English and anything else (small.en <-> small); non-tier models
    // are the user's explicit pick and stay put.
    const nowEnglish = v === "English";
    const tier = meta.model_tiers.find(
      (t) => t.model === settings.model || t.model_en === settings.model,
    );
    const patch: Partial<typeof settings> = { language: v };
    if (tier) patch.model = nowEnglish ? tier.model_en : tier.model;
    update(patch);
  };

  return (
    <div className="mx-auto max-w-3xl px-8 py-10">
      <h1 className="mb-6 text-2xl font-bold">Settings</h1>
      <div className="flex flex-col gap-4">
        <Card title="Language & task">
          <div className="grid grid-cols-2 gap-4">
            <SelectField
              label="Spoken language"
              value={settings.language}
              options={meta.languages.map(([n]) => ({ value: n, label: n }))}
              onChange={onLanguageChange}
              note="Setting it beats auto-detect on speed and accuracy."
            />
            <SelectField
              label="Task"
              value={settings.task}
              options={[
                { value: "transcribe", label: "Transcribe" },
                { value: "translate", label: "Translate into English" },
              ]}
              onChange={(v) => update({ task: v as typeof settings.task })}
            />
          </div>
        </Card>

        <Card title="Saving">
          <div className="grid grid-cols-2 gap-4">
            <SelectField
              label="Default save format"
              value={settings.output_format === "txt" ? "txt" : "docx"}
              options={[
                { value: "docx", label: "Word (.docx)" },
                { value: "txt", label: "Plain text (.txt)" },
              ]}
              onChange={(v) =>
                update({ output_format: v as "docx" | "txt" })
              }
              note="Transcripts save in this format. One-off PDFs come from the Save menu in review."
            />
            <SelectField
              label="Word layout"
              value={settings.doc_template}
              options={TEMPLATE_OPTIONS}
              onChange={(v) => update({ doc_template: v as typeof settings.doc_template })}
              note="Court and ERISP number every line on the page. Word only."
            />
          </div>
          <div className="mt-4 flex flex-col gap-2.5">
            <CheckField
              label="Colour each speaker's paragraphs"
              checked={settings.colour_speakers}
              disabled={settings.output_format === "txt"}
              onChange={(v) => update({ colour_speakers: v })}
              note={
                settings.output_format === "txt"
                  ? "Word and PDF only — a plain text file has nowhere to put a colour."
                  : "Shades every paragraph in its speaker's review-pane colour, so a saved or printed transcript reads the way the review pane does. Off by default: a transcript that goes on a court file should look like every other transcript unless you decide otherwise. It can also be switched per document from the review pane."
              }
            />
          </div>
        </Card>

        <Card title="Accuracy">
          <div className="flex flex-col gap-2.5">
            <CheckField
              label="Re-run sections that look like engine errors"
              checked={settings.auto_retranscribe}
              onChange={(v) => update({ auto_retranscribe: v })}
              note="After a transcription, the sections flagged as repeated or low-confidence text are transcribed a second time without conditioning — the setting that makes the engine repeat itself. The new text is kept only where the same check no longer objects to it, and every replacement can be undone in the review pane."
            />
          </div>
        </Card>

        <Card title="Updates">
          <div className="flex flex-col gap-2.5">
            <CheckField
              label="Check for new versions on launch"
              checked={settings.check_updates}
              onChange={(v) => update({ check_updates: v })}
              note="Asks GitHub once per launch whether a newer release exists. No transcripts, recordings or usage data leave your computer — turn it off to keep Transcribr fully offline."
            />
            <UpdateCheckRow />
          </div>
        </Card>

        <Card title="Transcribe page">
          <div className="flex flex-col gap-2.5">
            <CheckField
              label="Show the full Whisper model list"
              checked={settings.show_all_models}
              onChange={(v) => update({ show_all_models: v })}
              note="Adds the full model dropdown under the three-tier picker."
            />
            <CheckField
              label="Show the context / vocabulary hint field"
              checked={settings.show_prompt}
              onChange={(v) => update({ show_prompt: v })}
              note="A field that primes the engine with names, acronyms and place names. Powerful for jargon-heavy recordings, but priming can backfire — it stays hidden unless you want it."
            />
            <CheckField
              label="Condition on previous text"
              checked={settings.condition_on_previous_text}
              onChange={(v) => update({ condition_on_previous_text: v })}
              note="Feeds each 30-second chunk the text before it. Off by default: it tends to make transcripts less accurate, and one bad chunk could send the rest into repeated lines or lose all punctuation."
            />
            <CheckField
              label="Show the speaker detection option (experimental)"
              checked={settings.show_diarize}
              onChange={(v) => update({ show_diarize: v })}
              note="Adds a Speakers card to the Transcribe page that can suggest a speaker label per paragraph. Experimental — off and hidden by default."
            />
          </div>
        </Card>

        <Card title="Extra technical files">
          <p className="mb-3 text-xs text-muted">
            Optional sidecar files saved alongside every transcript. The
            subtitle and spreadsheet files follow your review edits; the
            JSON keeps the engine's raw output as the technical record.
          </p>
          <div className="grid grid-cols-2 gap-2.5">
            <CheckField label="JSON (raw engine result)" checked={settings.extra_json}
              onChange={(v) => update({ extra_json: v })} />
            <CheckField label="SRT subtitles" checked={settings.extra_srt}
              onChange={(v) => update({ extra_srt: v })} />
            <CheckField label="VTT subtitles" checked={settings.extra_vtt}
              onChange={(v) => update({ extra_vtt: v })} />
            <CheckField label="TSV spreadsheet" checked={settings.extra_tsv}
              onChange={(v) => update({ extra_tsv: v })} />
          </div>
        </Card>

        <Card title="Accuracy tuning (rarely needed)">
          <p className="mb-4 text-xs text-muted">
            The defaults were chosen by testing on recordings like yours —
            change them only to troubleshoot a specific problem.
          </p>
          <div className="grid grid-cols-2 gap-4">
            <SelectField
              label="Engine"
              value={settings.engine}
              options={meta.engines.map((e) => ({ value: e.name, label: e.name }))}
              onChange={(v) => update({ engine: v })}
              note="Automatic picks faster-whisper where it is installed: it is more accurate than mlx-whisper, which is quicker but cannot be stopped mid-run."
            />
            <SelectField
              label="Word-level timing & highlighting"
              value={settings.word_timestamps}
              options={[
                { value: "auto", label: "Automatic (recommended)" },
                { value: "on", label: "Always on" },
                { value: "off", label: "Always off (not recommended)" },
              ]}
              onChange={(v) =>
                update({ word_timestamps: v as "auto" | "on" | "off" })
              }
              note="Word timings enable the red/amber highlighting of uncertain words and sharpen paragraph breaks. Automatic records them on every engine. Always off saves a little time but can make telephone recordings less accurate, and leaves paragraphing and review without timings."
            />
            <CheckField
              label="Break paragraphs where the voice changes"
              checked={settings.voice_breaks}
              onChange={(v) => update({ voice_breaks: v })}
              note="Starts a new paragraph where the speaker's pitch changes sharply after a finished sentence — usually a different person. It catches many more speaker changes, at the cost of an occasional extra break when one person talks on."
            />
            <NumberField label="Speaker separation threshold"
              value={settings.diarize_threshold} min={0.2} max={0.9} step={0.05}
              onChange={(v) => update({ diarize_threshold: v })}
              note="Lower = readier to hear two similar voices as different people. Ignored when 'How many speakers?' is set." />
          </div>
          <details className="mt-4 rounded-lg border border-edge px-3 py-2">
            <summary className="cursor-pointer text-sm font-medium">
              Decoder settings
            </summary>
            <p className="mt-2 mb-4 text-xs text-muted">
              Whisper's internal search settings. Testing found no gain from
              changing them, and some changes make transcripts worse.
            </p>
            <div className="grid grid-cols-2 gap-4">
              <NumberField label="Temperature" value={settings.temperature}
                min={0} max={1} step={0.1}
                onChange={(v) => update({ temperature: v })}
                note="Leave at 0 for transcripts: the engine then always picks its most likely words." />
              <NumberField label="Beam size" value={settings.beam_size}
                min={1} max={20}
                onChange={(v) => update({ beam_size: v })}
                note="How many alternatives are weighed at each step. Higher = slightly more accurate, slower." />
              <NumberField label="Best of" value={settings.best_of}
                min={1} max={20}
                onChange={(v) => update({ best_of: v })}
                note="Only applies when temperature is above 0." />
              <NumberField label="Compression ratio threshold"
                value={settings.compression_ratio_threshold} step={0.1}
                onChange={(v) => update({ compression_ratio_threshold: v })}
                note="Whisper's own repetition guard. It works only together with a temperature above 0, so at the default it has no effect — Transcribr's own loop check and repair do that job." />
              <NumberField label="Log-probability threshold"
                value={settings.logprob_threshold} min={-10} max={0} step={0.1}
                onChange={(v) => update({ logprob_threshold: v })}
                note="Used with the no-speech threshold: a quiet chunk is skipped only when the engine is also this unsure of its words." />
              <NumberField label="No-speech threshold"
                value={settings.no_speech_threshold} min={0} max={1} step={0.05}
                onChange={(v) => update({ no_speech_threshold: v })}
                note="How readily quiet passages are skipped as silence." />
            </div>
          </details>
        </Card>

        <Card title="Appearance">
          <div className="grid grid-cols-2 gap-4">
            <SelectField
              label="Theme"
              value={settings.theme}
              options={[
                { value: "auto", label: "Follow the system" },
                { value: "light", label: "Light" },
                { value: "dark", label: "Dark" },
              ]}
              onChange={(v) => update({ theme: v as ThemeSetting })}
            />
          </div>
        </Card>

        <Card title="Acknowledgements">
          <p className="mb-3 text-xs text-muted">
            Transcribr is built on open-source software and openly licensed
            models: OpenAI's Whisper, faster-whisper, FFmpeg, sherpa-onnx,
            pyannote, NVIDIA's TitaNet and WeSpeaker among them. The notices
            list every one, with its licence.
          </p>
          <button
            className="rounded-lg border border-edge px-3 py-2 text-sm hover:bg-surface-2"
            onClick={() => void api.post("/api/notices/open", {}).catch(() => {})}
          >
            Open the third-party notices
          </button>
        </Card>

        <Card title="Troubleshooting">
          <button
            className="rounded-lg border border-edge px-3 py-2 text-sm hover:bg-surface-2"
            onClick={() => void api.post("/api/log/open", {}).catch(() => {})}
          >
            Open the log file
          </button>
          <p className="mt-2 text-xs text-muted">
            The last few lines usually show the cause when something
            misbehaves.
          </p>
        </Card>

        <section className="rounded-xl border border-edge bg-surface p-5">
          <ModelsView embedded />
        </section>
      </div>
    </div>
  );
}

/** Manual check, for when the launch check is off or the user wants to
 *  look again without restarting. */
function UpdateCheckRow() {
  const status = useUpdate((s) => s.status);
  const checking = status?.state === "checking";
  return (
    <div className="flex items-center gap-3">
      <button
        className="rounded-lg border border-edge px-3 py-1.5 text-xs font-medium disabled:opacity-50"
        disabled={checking}
        onClick={() => void useUpdate.getState().check()}
      >
        {checking ? "Checking…" : "Check now"}
      </button>
      <span className="text-xs text-muted">
        {status?.state === "available"
          ? `Version ${status.latest} is available (you have ${status.current}).`
          : status?.state === "current"
            ? `Transcribr ${status.current} is up to date.`
            : status?.state === "error"
              ? status.error
              : `Version ${status?.current ?? ""}`}
      </span>
    </div>
  );
}

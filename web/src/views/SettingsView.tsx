import { api } from "../api/client";
import { useApp } from "../state/store";
import { Card, CheckField, NumberField, SelectField } from "../components/fields";
import ModelsView from "./ModelsView";
import type { ThemeSetting } from "../api/types";

// The advanced-settings page: everything that shouldn't crowd the
// Transcribe page but still deserves a home. All values persist to
// settings.json like every other option.

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
              note="Transcripts save in this format. One-off PDFs come from the Export PDF button on the Review pane."
            />
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
              note="Feeds each chunk the text before it: more consistent style, but an early mistake can propagate. Leave on unless a transcript goes off the rails."
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
            The defaults are the ones tuned by Whisper's authors — change them
            only to troubleshoot a specific problem.
          </p>
          <div className="grid grid-cols-2 gap-4">
            <SelectField
              label="Engine"
              value={settings.engine}
              options={meta.engines.map((e) => ({ value: e.name, label: e.name }))}
              onChange={(v) => update({ engine: v })}
              note="Automatic picks the fastest engine installed on this computer."
            />
            <NumberField label="Temperature" value={settings.temperature}
              min={0} max={1} step={0.1}
              onChange={(v) => update({ temperature: v })}
              note="Leave at 0 for transcripts. The engine raises it by itself only if it gets stuck." />
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
              note="Hallucination guard — retries a chunk whose output looks too repetitive." />
            <NumberField label="Log-probability threshold"
              value={settings.logprob_threshold} min={-10} max={0} step={0.1}
              onChange={(v) => update({ logprob_threshold: v })}
              note="Retries a chunk when the engine's own confidence drops below this." />
            <NumberField label="No-speech threshold"
              value={settings.no_speech_threshold} min={0} max={1} step={0.05}
              onChange={(v) => update({ no_speech_threshold: v })}
              note="How readily quiet passages are skipped as silence." />
            <NumberField label="Speaker separation threshold"
              value={settings.diarize_threshold} min={0.2} max={0.9} step={0.05}
              onChange={(v) => update({ diarize_threshold: v })}
              note="Lower = readier to hear two similar voices as different people. Ignored when 'How many speakers?' is set." />
          </div>
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

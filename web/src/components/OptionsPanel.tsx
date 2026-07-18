import { useApp } from "../state/store";
import { useRun } from "../state/runStore";
import { Card, CheckField, Disclosure, NumberField, SelectField } from "./fields";
import type { ModelTier } from "../api/types";

function ModelTierPicker() {
  const meta = useApp((s) => s.meta)!;
  const settings = useApp((s) => s.settings)!;
  const update = useApp.getState().updateSettings;

  const english = settings.language === "English";
  const tierModel = (t: ModelTier) => (english ? t.model_en : t.model);
  const activeTier = meta.model_tiers.find((t) => tierModel(t) === settings.model);
  // A model outside the three tiers (picked before 0.9.0, or from the
  // full list) keeps the full dropdown visible so it stays explicable.
  const showAll = settings.show_all_models || (!activeTier && meta.model_tiers.length > 0);

  return (
    <div className="flex flex-col gap-2">
      <span className="text-xs font-medium tracking-wide text-muted">Model</span>
      <div role="radiogroup" className="flex flex-col gap-2">
        {meta.model_tiers.map((t) => {
          const selected = activeTier?.id === t.id;
          return (
            <button
              key={t.id}
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => update({ model: tierModel(t) })}
              className={`rounded-lg border px-3 py-2 text-left text-sm transition-colors ${
                selected
                  ? "border-accent bg-accent/10"
                  : "border-edge hover:bg-surface-2"
              }`}
            >
              <span className="flex items-baseline justify-between gap-2">
                <span className="font-medium">
                  {t.label}
                  {t.recommended && (
                    <span className="ml-1.5 text-xs font-normal text-accent">
                      recommended
                    </span>
                  )}
                </span>
                <span className="text-xs text-muted">{t.size}</span>
              </span>
              <span className="mt-0.5 block text-xs text-muted">{t.note}</span>
            </button>
          );
        })}
      </div>
      <CheckField
        label="Show all Whisper models"
        checked={settings.show_all_models}
        onChange={(v) => update({ show_all_models: v })}
        note="Every download is managed from the Models view in the sidebar."
      />
      {showAll && (
        <SelectField
          label="Whisper model"
          value={settings.model}
          options={meta.models.map((m) => ({ value: m, label: m }))}
          onChange={(v) => update({ model: v })}
          note="Models ending in .en are English-only and slightly more accurate there."
        />
      )}
    </div>
  );
}

export default function OptionsPanel() {
  const meta = useApp((s) => s.meta)!;
  const settings = useApp((s) => s.settings);
  if (!settings) return null;
  const update = useApp.getState().updateSettings;

  const onLanguageChange = (v: string) => {
    // Keep the chosen tier when the language flips between English and
    // anything else (small.en <-> small); non-tier models are the
    // user's explicit pick and stay put.
    const nowEnglish = v === "English";
    const tier = meta.model_tiers.find(
      (t) => t.model === settings.model || t.model_en === settings.model,
    );
    const patch: Partial<typeof settings> = { language: v };
    if (tier) patch.model = nowEnglish ? tier.model_en : tier.model;
    update(patch);
  };

  return (
    <div className="flex flex-col gap-4">
      <Card title="Output">
        <div className="mb-4 flex gap-4">
          {(["txt", "docx", "pdf"] as const).map((fmt) => (
            <label key={fmt} className="flex cursor-pointer items-center gap-1.5 text-sm">
              <input
                type="radio"
                name="fmt"
                className="accent-(--accent)"
                checked={settings.output_format === fmt}
                onChange={() => {
                  update({ output_format: fmt });
                  useRun.getState().onFormatChanged(fmt);
                }}
              />
              .{fmt}
            </label>
          ))}
        </div>
        <div className="flex flex-col gap-2.5">
          <CheckField
            label="Show timestamps in output"
            checked={settings.show_timestamp}
            onChange={(v) => update({ show_timestamp: v })}
          />
          <CheckField
            label="Review and label speakers before saving"
            checked={settings.review}
            onChange={(v) => update({ review: v })}
            note="Applies to single files — batches always save directly."
          />
        </div>
      </Card>

      <Card title="Model & language">
        <div className="flex flex-col gap-4">
          <ModelTierPicker />
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
        </div>
      </Card>

      <Card title="Speakers">
        <div className="flex flex-col gap-4">
          <CheckField
            label="Detect speakers automatically (experimental)"
            checked={settings.diarize && meta.diarize_available}
            disabled={!meta.diarize_available}
            onChange={(v) => update({ diarize: v })}
            note={
              meta.diarize_available
                ? "Experimental: listens for different voices and suggests a speaker " +
                  "label for each paragraph, for you to check in Review. Paragraph " +
                  "boundaries themselves always come from the paragraph settings " +
                  "below. Downloads a small helper model (~33 MB) the first time; " +
                  "everything still runs on this computer."
                : "Needs the sherpa-onnx package — re-run the installer to add it."
            }
          />
          {settings.diarize && meta.diarize_available && (
            <div className="grid grid-cols-2 gap-4">
              <NumberField
                label="How many speakers?"
                value={settings.num_speakers}
                min={0}
                max={9}
                onChange={(v) => update({ num_speakers: Math.max(0, Math.round(v)) })}
                note="Leave at 0 to work it out from the audio. Setting it helps when you know."
              />
              <SelectField
                label="Voice-matching model"
                value={settings.diarize_model}
                options={meta.diarize_models.map((m) => ({
                  value: m.id,
                  label: `${m.label} (${m.size})`,
                }))}
                onChange={(v) => update({ diarize_model: v })}
                note={
                  meta.diarize_models.find((m) => m.id === settings.diarize_model)
                    ?.note ??
                  "If two people keep sharing one label, try a different model and run again."
                }
              />
            </div>
          )}
        </div>
      </Card>

      <Disclosure title="Paragraphs & extra outputs">
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-4">
            <NumberField
              label="Start a new paragraph after a silence of (seconds)"
              value={settings.gap}
              min={0}
              max={10}
              step={0.1}
              onChange={(v) => update({ gap: v })}
              note="Lower for rapid dialogue, higher for monologue. Sentence endings also break when followed by a pause of 40% of this."
            />
          </div>
          <div>
            <span className="mb-2 block text-xs font-medium tracking-wide text-muted">
              Extra technical files, saved alongside the transcript
            </span>
            <div className="grid grid-cols-2 gap-2.5">
              <CheckField label="JSON (full engine result)" checked={settings.extra_json}
                onChange={(v) => update({ extra_json: v })} />
              <CheckField label="SRT subtitles" checked={settings.extra_srt}
                onChange={(v) => update({ extra_srt: v })} />
              <CheckField label="VTT subtitles" checked={settings.extra_vtt}
                onChange={(v) => update({ extra_vtt: v })} />
              <CheckField label="TSV spreadsheet" checked={settings.extra_tsv}
                onChange={(v) => update({ extra_tsv: v })} />
            </div>
          </div>
        </div>
      </Disclosure>

      <Disclosure title="Accuracy tuning (rarely needed)">
        <p className="mb-4 text-xs text-muted">
          The defaults are the ones tuned by Whisper's authors — change them only
          to troubleshoot a specific problem.
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
        <div className="mt-4 flex flex-col gap-2.5">
          <CheckField label="Condition on previous text"
            checked={settings.condition_on_previous_text}
            onChange={(v) => update({ condition_on_previous_text: v })}
            note="Feeds each chunk the text before it: more consistent style, but an early mistake can propagate." />
          <CheckField label="Highlight low-confidence words in review"
            checked={settings.highlight_confidence}
            onChange={(v) => update({ highlight_confidence: v })}
            note="Shades words the engine was unsure about so you know where to listen. (Word-level timestamps are always recorded — they also sharpen paragraph breaks.)" />
        </div>
      </Disclosure>
    </div>
  );
}

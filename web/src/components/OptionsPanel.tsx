import { useApp } from "../state/store";
import { Card, CheckField, NumberField, SelectField } from "./fields";
import type { ModelTier } from "../api/types";

// The Transcribe page's options: just the Quick settings and Speakers
// cards. Everything advanced lives on the Settings page; the save-time
// choices (format, timestamps) live on the Review pane.

function ModelTierPicker() {
  const meta = useApp((s) => s.meta)!;
  const settings = useApp((s) => s.settings)!;
  const update = useApp.getState().updateSettings;

  const english = settings.language === "English";
  const tierModel = (t: ModelTier) => (english ? t.model_en : t.model);
  const activeTier = meta.model_tiers.find((t) => tierModel(t) === settings.model);
  // The full dropdown appears when enabled from Settings, or when the
  // current model isn't one of the three tiers (so it stays explicable).
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
      <Card title="Quick settings">
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
            <NumberField
              label="Start a new paragraph after a silence of (seconds)"
              value={settings.gap}
              min={0}
              max={10}
              step={0.1}
              onChange={(v) => update({ gap: v })}
              note="Lower for rapid dialogue, higher for monologue. Sentence endings also break when followed by a pause of 40% of this. Works alongside speaker detection."
            />
          </div>
          <CheckField
            label="Condition on previous text"
            checked={settings.condition_on_previous_text}
            onChange={(v) => update({ condition_on_previous_text: v })}
            note="Feeds each chunk the text before it: more consistent style, but an early mistake can propagate. Try turning it off if a transcript goes off the rails."
          />
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
                  "above. Downloads a small helper model (~33 MB) the first time; " +
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
    </div>
  );
}

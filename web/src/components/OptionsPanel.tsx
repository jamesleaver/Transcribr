import { useState } from "react";
import { useApp } from "../state/store";
import { Card, CheckField, NumberField, SelectField, inputCls } from "./fields";
import type { ModelTier, Preset } from "../api/types";

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
          options={meta.models
            // The tiny models are too inaccurate for this work, worst of
            // all in a busy room: not offered unless already chosen.
            .filter((m) => !m.startsWith("tiny") || m === settings.model)
            .map((m) => ({ value: m, label: m }))}
          onChange={(v) => update({ model: v })}
          note="Models ending in .en are English-only and slightly more accurate there."
        />
      )}
    </div>
  );
}

/** Recording type: the four things that genuinely differ between an
 *  ERISP and a dictation (transcribr/presets.py). Everything else the
 *  user has set is left alone, and the picker says "Custom" rather than
 *  naming a preset that is not what is actually set. */
type Settings = NonNullable<ReturnType<typeof useApp.getState>["settings"]>;
type Meta = NonNullable<ReturnType<typeof useApp.getState>["meta"]>;

function presetValues(p: Preset, english: boolean) {
  return {
    model: english ? p.model_en : p.model,
    gap: p.gap,
    num_speakers: p.num_speakers,
    voice_breaks: p.voice_breaks,
  };
}

/** The recording type whose values are exactly what is set, if any. */
function activePreset(meta: Meta, settings: Settings): Preset | undefined {
  const english = settings.language === "English";
  return (meta.presets ?? []).find((p) =>
    Object.entries(presetValues(p, english)).every(
      ([k, v]) => settings[k as keyof Settings] === v,
    ),
  );
}

function PresetPicker() {
  const meta = useApp((s) => s.meta)!;
  const settings = useApp((s) => s.settings)!;
  const update = useApp.getState().updateSettings;
  const presets = meta.presets ?? [];
  if (!presets.length) return null;

  const english = settings.language === "English";
  const valuesFor = (p: Preset) => presetValues(p, english);
  const active = activePreset(meta, settings);
  const note = active?.note ?? "Your own settings.";

  return (
    <div className="flex flex-col gap-1.5">
      <SelectField
        label="Recording type"
        value={active?.id ?? "custom"}
        options={[
          ...presets.map((p) => ({ value: p.id, label: p.label })),
          ...(active ? [] : [{ value: "custom", label: "Custom" }]),
        ]}
        onChange={(v) => {
          const p = presets.find((x) => x.id === v);
          if (p) void update(valuesFor(p));
        }}
      />
      <span className="text-xs text-muted">{note}</span>
    </div>
  );
}

/** One line saying what the recording type has set. */
function Summary() {
  const meta = useApp((s) => s.meta)!;
  const settings = useApp((s) => s.settings)!;
  const english = settings.language === "English";
  const tier = meta.model_tiers.find(
    (t) => (english ? t.model_en : t.model) === settings.model,
  );
  // A recording type's own description already says what it sets.
  const custom = !activePreset(meta, settings);
  const parts = [
    custom ? (tier ? tier.label : settings.model) : null,
    custom ? `new paragraph after ${settings.gap} s of silence` : null,
    settings.title?.trim() ? `heading “${settings.title.trim()}”` : null,
  ].filter(Boolean);
  return <span className="text-xs text-muted">{parts.join(" · ")}</span>;
}

export default function OptionsPanel() {
  const meta = useApp((s) => s.meta)!;
  const settings = useApp((s) => s.settings);
  // The detail opens by itself when something in it would otherwise be
  // set out of sight: a heading, or a model no recording type uses.
  const [more, setMore] = useState(false);
  if (!settings) return null;
  const update = useApp.getState().updateSettings;
  const showMore = more || !!settings.title?.trim();

  return (
    <div className="flex flex-col gap-4">
      <Card title="Recording">
        <div className="flex flex-col gap-3">
          <PresetPicker />
          <div className="flex items-baseline justify-between gap-3">
            <Summary />
            <button
              className="shrink-0 text-xs font-medium text-accent hover:underline"
              onClick={() => setMore((v) => !v)}
            >
              {showMore ? "Done" : "Change"}
            </button>
          </div>
          {showMore && (
            <div className="flex flex-col gap-4 border-t border-edge pt-4">
              <ModelTierPicker />
              <NumberField
                label="New paragraph after a silence of (seconds)"
                value={settings.gap}
                min={0}
                max={10}
                step={0.1}
                onChange={(v) => update({ gap: v })}
              />
              <label className="flex flex-col gap-1.5">
                <span className="text-xs font-medium tracking-wide text-muted">
                  Heading (blank: the file name; you can change it in review)
                </span>
                <input
                  className={inputCls}
                  value={settings.title ?? ""}
                  onChange={(e) => update({ title: e.target.value })}
                />
              </label>
            </div>
          )}
        </div>
      </Card>

      {settings.show_diarize && (
      <Card title="Speakers">
        <div className="flex flex-col gap-4">
          <CheckField
            label="Detect speakers automatically (experimental)"
            checked={settings.diarize && meta.diarize_available}
            disabled={!meta.diarize_available}
            onChange={(v) => update({ diarize: v })}
            note={
              meta.diarize_available
                ? "Suggests who said each paragraph, for you to check. Recordings " +
                  "with a channel per speaker are labelled by channel instead."
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
                note="0 lets it guess, but it is far more accurate when told."
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
      )}
    </div>
  );
}

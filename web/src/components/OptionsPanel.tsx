import { useApp } from "../state/store";
import { useRun } from "../state/runStore";
import { Card, CheckField, Disclosure, NumberField, SelectField } from "./fields";

export default function OptionsPanel() {
  const meta = useApp((s) => s.meta)!;
  const settings = useApp((s) => s.settings);
  if (!settings) return null;
  const update = useApp.getState().updateSettings;

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

      <Card title="Engine">
        <div className="grid grid-cols-2 gap-4">
          <SelectField
            label="Engine"
            value={settings.engine}
            options={meta.engines.map((e) => ({ value: e.name, label: e.name }))}
            onChange={(v) => update({ engine: v })}
          />
          <SelectField
            label="Model"
            value={settings.model}
            options={meta.models.map((m) => ({ value: m, label: m }))}
            onChange={(v) => update({ model: v })}
          />
          <SelectField
            label="Language"
            value={settings.language}
            options={meta.languages.map(([n]) => ({ value: n, label: n }))}
            onChange={(v) => update({ language: v })}
          />
          <SelectField
            label="Task"
            value={settings.task}
            options={[
              { value: "transcribe", label: "transcribe" },
              { value: "translate", label: "translate" },
            ]}
            onChange={(v) => update({ task: v as typeof settings.task })}
          />
        </div>
      </Card>

      <Disclosure title="Paragraphs & extra outputs">
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-4">
            <NumberField
              label="Paragraph gap (seconds of silence)"
              value={settings.gap}
              min={0}
              max={10}
              step={0.1}
              onChange={(v) => update({ gap: v })}
            />
          </div>
          <div className="grid grid-cols-2 gap-2.5">
            <CheckField label="JSON (full result)" checked={settings.extra_json}
              onChange={(v) => update({ extra_json: v })} />
            <CheckField label="SRT subtitles" checked={settings.extra_srt}
              onChange={(v) => update({ extra_srt: v })} />
            <CheckField label="VTT subtitles" checked={settings.extra_vtt}
              onChange={(v) => update({ extra_vtt: v })} />
            <CheckField label="TSV" checked={settings.extra_tsv}
              onChange={(v) => update({ extra_tsv: v })} />
          </div>
        </div>
      </Disclosure>

      <Disclosure title="Advanced decoding">
        <div className="grid grid-cols-2 gap-4">
          <NumberField label="Temperature" value={settings.temperature}
            min={0} max={1} step={0.1}
            onChange={(v) => update({ temperature: v })} />
          <NumberField label="Beam size" value={settings.beam_size}
            min={1} max={20}
            onChange={(v) => update({ beam_size: v })} />
          <NumberField label="Best of" value={settings.best_of}
            min={1} max={20}
            onChange={(v) => update({ best_of: v })} />
          <NumberField label="Compression ratio threshold"
            value={settings.compression_ratio_threshold} step={0.1}
            onChange={(v) => update({ compression_ratio_threshold: v })} />
          <NumberField label="Logprob threshold"
            value={settings.logprob_threshold} min={-10} max={0} step={0.1}
            onChange={(v) => update({ logprob_threshold: v })} />
          <NumberField label="No-speech threshold"
            value={settings.no_speech_threshold} min={0} max={1} step={0.05}
            onChange={(v) => update({ no_speech_threshold: v })} />
        </div>
        <div className="mt-4 flex flex-col gap-2.5">
          <CheckField label="Condition on previous text"
            checked={settings.condition_on_previous_text}
            onChange={(v) => update({ condition_on_previous_text: v })} />
          <CheckField label="Word-level timestamps"
            checked={settings.word_timestamps}
            onChange={(v) => update({ word_timestamps: v })} />
          <CheckField label="Highlight low-confidence words in review"
            checked={settings.highlight_confidence}
            onChange={(v) => update({ highlight_confidence: v })}
            note="Enables word-level timestamps for the run." />
        </div>
      </Disclosure>
    </div>
  );
}

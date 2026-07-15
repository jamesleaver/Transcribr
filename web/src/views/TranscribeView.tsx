import { useApp } from "../state/store";

// P0 placeholder: proves live backend data (engines, models, languages)
// and the settings round-trip (selects write through the debounced PUT).
// The real Transcribe surface — drop zone, batch queue, options cards,
// run bar — lands in Phase 2.

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-medium tracking-wide text-muted">{label}</span>
      {children}
    </label>
  );
}

const selectCls =
  "rounded-lg border border-edge bg-surface px-3 py-2 text-sm text-fg " +
  "focus:border-accent focus:outline-none";

export default function TranscribeView() {
  const meta = useApp((s) => s.meta)!;
  const settings = useApp((s) => s.settings);
  const update = useApp((s) => s.updateSettings);

  if (!settings) return null;

  return (
    <div className="mx-auto max-w-3xl px-8 py-10">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold">Transcribe</h1>
        <p className="mt-1 text-sm text-muted">
          Phase 0 preview — engine data and settings below are live from the
          Python backend; the full transcribe surface arrives in Phase 2.
        </p>
      </header>

      <section className="grid grid-cols-2 gap-5 rounded-xl border border-edge bg-surface p-6">
        <Field label="Engine">
          <select
            className={selectCls}
            value={settings.engine}
            onChange={(e) => update({ engine: e.target.value })}
          >
            {meta.engines.map((e) => (
              <option key={e.key} value={e.name}>
                {e.name}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Model">
          <select
            className={selectCls}
            value={settings.model}
            onChange={(e) => update({ model: e.target.value })}
          >
            {meta.models.map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>
        </Field>

        <Field label="Language">
          <select
            className={selectCls}
            value={settings.language}
            onChange={(e) => update({ language: e.target.value })}
          >
            {meta.languages.map(([name]) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        </Field>

        <Field label="Task">
          <select
            className={selectCls}
            value={settings.task}
            onChange={(e) => update({ task: e.target.value as typeof settings.task })}
          >
            <option value="transcribe">transcribe</option>
            <option value="translate">translate</option>
          </select>
        </Field>
      </section>

      <section className="mt-5 rounded-xl border border-edge bg-surface p-6">
        <h2 className="mb-3 text-sm font-semibold">Speaker palette (from Python)</h2>
        <div className="flex gap-2">
          {Array.from({ length: 9 }, (_, i) => String(i + 1)).map((slot) => (
            <div
              key={slot}
              className="flex h-9 w-9 items-center justify-center rounded-lg text-xs font-semibold"
              style={{ background: `var(--speaker-${slot})`, color: "var(--badge-fg)" }}
            >
              {slot}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

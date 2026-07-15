import { useEffect, useState } from "react";
import type { EngineModels, ModelInfo } from "../api/types";
import { fmtBytes, useModels } from "../state/modelsStore";

// The Models manager: what's cached per engine (with sizes), plus
// download / uninstall. Each engine caches its own copy of a model, so
// the same name can occupy disk more than once — the view groups by
// engine to make that visible, and shows a running total.

function ActiveDownloadBanner() {
  const job = useModels((s) => s.job);
  const cancel = useModels((s) => s.cancel);
  if (!job) return null;
  const indeterminate = !job.total || job.phase === "starting";
  return (
    <div className="mb-6 rounded-xl border border-edge bg-surface p-4">
      <div className="mb-1 flex items-baseline justify-between gap-3">
        <span className="truncate text-sm font-medium">
          <span className="text-accent">Downloading</span> {job.model}
        </span>
        <span className="shrink-0 text-xs tabular-nums text-muted">
          {indeterminate ? "" : `${Math.round(job.pct)}%`}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
        {indeterminate ? (
          <div className="h-full w-1/3 animate-progress-indeterminate rounded-full bg-accent" />
        ) : (
          <div
            className="h-full rounded-full bg-accent transition-[width] duration-300"
            style={{ width: `${job.pct}%` }}
          />
        )}
      </div>
      <div className="mt-2 flex items-center justify-between gap-3">
        <span className="truncate text-xs text-muted">{job.status_text}</span>
        <button
          className="shrink-0 rounded-lg border border-edge px-2.5 py-1 text-xs font-medium hover:bg-surface-2"
          onClick={() => void cancel()}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

function ModelRow({
  engineKey,
  m,
  busy,
  isDownloading,
}: {
  engineKey: string;
  m: ModelInfo;
  busy: boolean;
  isDownloading: boolean;
}) {
  const download = useModels((s) => s.download);
  const uninstall = useModels((s) => s.uninstall);
  const btn =
    "shrink-0 rounded-lg border border-edge px-2.5 py-1 text-xs font-medium hover:bg-surface-2 disabled:opacity-40";

  return (
    <li className="flex items-center gap-3 px-4 py-2.5">
      <div className="min-w-0 flex-1">
        <span className="text-sm font-medium">{m.model}</span>
        {m.aliases.length > 0 && (
          <span className="ml-1.5 text-xs text-muted">
            or {m.aliases.join(", ")}
          </span>
        )}
        {m.custom && (
          <span className="ml-2 rounded bg-surface-2 px-1.5 py-0.5 text-[10px] font-medium text-muted">
            custom
          </span>
        )}
        <span className="mt-0.5 block text-xs text-muted">
          {isDownloading
            ? "downloading…"
            : m.installed
              ? fmtBytes(m.size)
              : "not downloaded"}
        </span>
      </div>
      {m.installed ? (
        <button
          className={`${btn} hover:border-red-400 hover:text-red-400`}
          disabled={busy}
          onClick={() => void uninstall(engineKey, m.model, m.size)}
        >
          Uninstall
        </button>
      ) : (
        <button
          className={btn}
          disabled={busy}
          onClick={() => void download(engineKey, m.model)}
        >
          Download
        </button>
      )}
    </li>
  );
}

function CustomDownload({
  engineKey,
  busy,
}: {
  engineKey: string;
  busy: boolean;
}) {
  const download = useModels((s) => s.download);
  const [value, setValue] = useState("");
  const submit = () => {
    if (!value.trim()) return;
    void download(engineKey, value);
    setValue("");
  };
  return (
    <div className="flex gap-2 border-t border-edge px-4 py-3">
      <input
        className="min-w-0 flex-1 rounded-lg border border-edge bg-surface px-3 py-1.5 text-sm focus:border-accent focus:outline-none disabled:opacity-40"
        placeholder="New model — a name or Hugging Face repo (e.g. mlx-community/whisper-large-v3-turbo)"
        value={value}
        disabled={busy}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") submit();
        }}
      />
      <button
        className="shrink-0 rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-accent-fg disabled:opacity-40"
        disabled={busy || !value.trim()}
        onClick={submit}
      >
        Download
      </button>
    </div>
  );
}

function EngineCard({
  engine,
  busy,
  jobKey,
}: {
  engine: EngineModels;
  busy: boolean;
  jobKey: string | null;
}) {
  const [open, setOpen] = useState(true);

  const builtin = engine.models.filter((m) => !m.custom);
  const installedBuiltin = builtin.filter((m) => m.installed).length;
  const customCount = engine.models.length - builtin.length;
  const summary =
    `${installedBuiltin} of ${builtin.length} installed` +
    (customCount > 0 ? ` · ${customCount} custom` : "") +
    ` · ${engine.total > 0 ? fmtBytes(engine.total) : "nothing cached"}`;

  return (
    <details
      open={open}
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}
      className="overflow-hidden rounded-xl border border-edge bg-surface"
    >
      <summary className="flex cursor-pointer select-none items-baseline justify-between gap-3 px-4 py-3 marker:content-none">
        <span className="flex items-baseline gap-2">
          <span
            className={`text-muted transition-transform ${open ? "rotate-90" : ""}`}
          >
            ▸
          </span>
          <span className="text-sm font-semibold">{engine.name}</span>
        </span>
        <span className="shrink-0 text-xs tabular-nums text-muted">{summary}</span>
      </summary>
      <ul className="divide-y divide-edge border-t border-edge">
        {engine.models.map((m) => (
          <ModelRow
            key={`${engine.key}:${m.model}`}
            engineKey={engine.key}
            m={m}
            busy={busy}
            isDownloading={jobKey === `${engine.key}:${m.model}`}
          />
        ))}
      </ul>
      {engine.supports_custom && <CustomDownload engineKey={engine.key} busy={busy} />}
    </details>
  );
}

export default function ModelsView() {
  const data = useModels((s) => s.data);
  const loaded = useModels((s) => s.loaded);
  const loading = useModels((s) => s.loading);
  const job = useModels((s) => s.job);
  const refresh = useModels((s) => s.refresh);

  useEffect(() => {
    if (!loaded) void refresh();
  }, [loaded, refresh]);

  // `data.busy` only refreshes on a models event; an in-flight job arrives
  // live via model_progress, so fold it in to disable actions immediately.
  const busy = (data?.busy ?? false) || job !== null;
  const jobKey = job ? `${job.engine}:${job.model}` : null;

  return (
    <div className="mx-auto max-w-3xl px-8 py-10">
      <header className="mb-6 flex items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Models</h1>
          <p className="mt-1 text-sm text-muted">
            Downloaded Whisper models and the space they use. Each engine
            keeps its own copy, so the same model can be stored more than once.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          {data && (
            <span className="text-xs text-muted">
              Total on disk:{" "}
              <span className="font-semibold text-fg tabular-nums">
                {fmtBytes(data.total)}
              </span>
            </span>
          )}
          <button
            className="rounded-lg border border-edge px-3 py-2 text-xs font-medium hover:bg-surface-2 disabled:opacity-40"
            disabled={loading}
            onClick={() => void refresh()}
          >
            Refresh
          </button>
        </div>
      </header>

      <ActiveDownloadBanner />

      {!data ? (
        <div className="rounded-xl border border-dashed border-edge p-10 text-center text-sm text-muted">
          {loading ? "Reading the model cache…" : "No model information yet."}
        </div>
      ) : data.engines.length === 0 ? (
        <div className="rounded-xl border border-dashed border-edge p-10 text-center text-sm text-muted">
          No transcription engine is installed, so there are no models to manage.
        </div>
      ) : (
        <div className="flex flex-col gap-5">
          {data.engines.map((engine) => (
            <EngineCard
              key={engine.key}
              engine={engine}
              busy={busy}
              jobKey={jobKey}
            />
          ))}
        </div>
      )}

      {data && (
        <p className="mt-6 text-[11px] leading-relaxed text-muted">
          Cache locations — openai-whisper: <code>{data.whisper_cache}</code>;
          faster-whisper / mlx-whisper: <code>{data.hf_cache}</code>.
        </p>
      )}
    </div>
  );
}

import { useState, type JSX } from "react";
import { api } from "../api/client";
import { alertDialog } from "../state/dialogs";
import { useApp, type View } from "../state/store";
import { useReview } from "../state/reviewStore";

function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
  );
}

const ICONS = {
  transcribe: "M12 4a3 3 0 0 1 3 3v5a3 3 0 1 1-6 0V7a3 3 0 0 1 3-3zm-6 8a6 6 0 0 0 12 0M12 18v3",
  review: "M4 6h16M4 10h16M4 14h10M4 18h7",
  library: "M4 5a2 2 0 0 1 2-2h9l5 5v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5zm11-2v5h5",
  models: "M4 7c0-1.1 3.6-2 8-2s8 .9 8 2-3.6 2-8 2-8-.9-8-2zm0 0v10c0 1.1 3.6 2 8 2s8-.9 8-2V7M4 12c0 1.1 3.6 2 8 2s8-.9 8-2",
  settings: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zm8.5 3a8 8 0 0 0-.2-1.7l2-1.5-2-3.5-2.3 1a8 8 0 0 0-2.9-1.7L14.7 2h-5.4l-.4 2.6a8 8 0 0 0-2.9 1.7l-2.3-1-2 3.5 2 1.5A8 8 0 0 0 3.5 12c0 .6.1 1.1.2 1.7l-2 1.5 2 3.5 2.3-1a8 8 0 0 0 2.9 1.7l.4 2.6h5.4l.4-2.6a8 8 0 0 0 2.9-1.7l2.3 1 2-3.5-2-1.5c.1-.6.2-1.1.2-1.7z",
};

function NavButton({ view, label, icon, disabled }: {
  view: View; label: string; icon: JSX.Element; disabled?: boolean;
}) {
  const active = useApp((s) => s.view) === view;
  return (
    <button
      disabled={disabled}
      onClick={() => useApp.getState().setView(view)}
      title={disabled ? `${label} — nothing open yet` : label}
      className={[
        "flex w-full flex-col items-center gap-1 rounded-lg px-1 py-2.5 text-[11px] font-medium transition-colors",
        active ? "bg-accent/15 text-accent" : "text-muted hover:bg-surface-2 hover:text-fg",
        disabled ? "cursor-not-allowed opacity-40 hover:bg-transparent hover:text-muted" : "",
      ].join(" ")}
    >
      {icon}
      {label}
    </button>
  );
}

/** Minimal markdown rendering for the in-app README viewer: headings,
 *  bold, inline code, bullets and fenced blocks. Anything fancier
 *  (tables, images) falls back to plain text lines. */
function MarkdownLite({ text }: { text: string }) {
  const blocks: JSX.Element[] = [];
  const lines = text.split("\n");
  let i = 0;
  let key = 0;

  const inline = (s: string): (string | JSX.Element)[] =>
    s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, n) => {
      if (part.startsWith("**") && part.endsWith("**"))
        return <strong key={n}>{part.slice(2, -2)}</strong>;
      if (part.startsWith("`") && part.endsWith("`"))
        return (
          <code key={n} className="rounded bg-surface-2 px-1 font-mono text-[0.9em]">
            {part.slice(1, -1)}
          </code>
        );
      return part;
    });

  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const buf: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].startsWith("```")) {
        buf.push(lines[i]);
        i += 1;
      }
      i += 1;
      blocks.push(
        <pre key={key++} className="overflow-x-auto rounded-lg bg-surface-2 p-3 font-mono text-xs">
          {buf.join("\n")}
        </pre>,
      );
      continue;
    }
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    if (h) {
      const cls =
        h[1].length === 1
          ? "mt-6 text-xl font-bold"
          : h[1].length === 2
            ? "mt-5 text-lg font-semibold"
            : "mt-4 text-base font-semibold";
      blocks.push(<div key={key++} className={cls}>{inline(h[2])}</div>);
      i += 1;
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
        i += 1;
      }
      blocks.push(
        <ul key={key++} className="list-disc pl-5">
          {items.map((it, n) => <li key={n}>{inline(it)}</li>)}
        </ul>,
      );
      continue;
    }
    if (line.trim() === "") {
      blocks.push(<div key={key++} className="h-2" />);
      i += 1;
      continue;
    }
    blocks.push(<p key={key++}>{inline(line)}</p>);
    i += 1;
  }
  return <div className="flex flex-col gap-1 text-sm leading-relaxed">{blocks}</div>;
}

function ReadmeViewer({ text, onClose }: { text: string; onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6"
      onClick={onClose}
    >
      <div
        className="flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-edge bg-surface shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-edge px-5 py-3">
          <h2 className="text-sm font-semibold">README</h2>
          <button
            className="rounded-lg border border-edge px-3 py-1.5 text-xs hover:bg-surface-2"
            onClick={onClose}
          >
            Close (Esc)
          </button>
        </div>
        <div className="overflow-y-auto px-6 py-4">
          <MarkdownLite text={text} />
        </div>
      </div>
    </div>
  );
}

export default function Sidebar() {
  const meta = useApp((s) => s.meta);
  const sse = useApp((s) => s.sse);
  const hasReview = useReview((s) => s.doc !== null);
  const [readme, setReadme] = useState<string | null>(null);

  const openReadme = () =>
    void api
      .get<{ text: string }>("/api/readme")
      .then((r) => setReadme(r.text))
      .catch(() => {});

  return (
    <aside className="flex w-[72px] shrink-0 flex-col border-r border-edge bg-surface px-2 py-3">
      <div className="mb-4 text-center text-lg font-bold text-accent" title="Transcribr">
        T
      </div>

      <nav className="flex flex-col gap-1">
        <NavButton view="transcribe" label="Transcribe" icon={<Icon d={ICONS.transcribe} />} />
        <NavButton view="review" label="Review" icon={<Icon d={ICONS.review} />}
          disabled={!hasReview} />
        <NavButton view="library" label="Library" icon={<Icon d={ICONS.library} />} />
        <NavButton view="models" label="Models" icon={<Icon d={ICONS.models} />} />
        <NavButton view="settings" label="Settings" icon={<Icon d={ICONS.settings} />} />
      </nav>

      <div className="mt-auto flex flex-col items-center gap-2">
        <button
          onClick={() =>
            void alertDialog(
              "Keyboard shortcuts",
              "In the review workspace:",
              [
                "1–9      assign speaker (auto-advances)",
                "0        clear speaker",
                "M        merge with previous paragraph",
                "N        next paragraph needing attention",
                "P        play/stop the selected paragraph",
                "Enter    edit paragraph (Enter commits, Esc cancels)",
                "double-click a word to split there",
                "⌘Z / ⌘⇧Z / ⌘Y   undo / redo",
                "⌘F       find",
              ].join("\n"),
            )
          }
          title="Keyboard shortcuts"
          className="rounded-lg p-2 text-muted transition-colors hover:bg-surface-2 hover:text-fg"
        >
          <Icon d="M9 9a3 3 0 1 1 4.6 2.5c-.9.6-1.6 1.1-1.6 2.5m0 3.5v.01" />
        </button>
        <button
          onClick={() =>
            void alertDialog("About Transcribr", meta?.about_text ?? "")
          }
          title="About Transcribr"
          className="rounded-lg p-2 text-muted transition-colors hover:bg-surface-2 hover:text-fg"
        >
          <Icon d="M12 8v.01M12 11v5m0 5a9 9 0 1 1 0-18 9 9 0 0 1 0 18z" />
        </button>
        <button
          onClick={() => void api.post("/api/log/open", {}).catch(() => {})}
          title="Open the log file"
          className="rounded-lg p-2 text-muted transition-colors hover:bg-surface-2 hover:text-fg"
        >
          <Icon d="M6 3h8l4 4v14H6V3zm8 0v4h4M9 12h6M9 16h6" />
        </button>
        {meta?.readme_available && (
          <button
            onClick={openReadme}
            title="View the README"
            className="rounded-lg p-2 text-muted transition-colors hover:bg-surface-2 hover:text-fg"
          >
            <Icon d="M4 5a2 2 0 0 1 2-2h5v18H6a2 2 0 0 0-2 2V5zm7-2h5a2 2 0 0 1 2 2v18a2 2 0 0 0-2-2h-5V3z" />
          </button>
        )}
        <div
          title={sse === "open" ? "Connected" : sse === "down" ? "Reconnecting…" : "Connecting…"}
          className={[
            "h-2 w-2 rounded-full",
            sse === "open" ? "bg-emerald-500" : sse === "down" ? "bg-red-400" : "bg-amber-400",
          ].join(" ")}
        />
        <div className="text-[10px] text-muted">{meta ? `v${meta.version}` : ""}</div>
      </div>

      {readme !== null && (
        <ReadmeViewer text={readme} onClose={() => setReadme(null)} />
      )}
    </aside>
  );
}

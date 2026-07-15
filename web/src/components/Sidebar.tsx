import type { JSX } from "react";
import { useApp, type View } from "../state/store";
import { useReview } from "../state/reviewStore";
import type { ThemeSetting } from "../api/types";

const THEME_CYCLE: ThemeSetting[] = ["auto", "light", "dark"];
const THEME_LABEL: Record<ThemeSetting, string> = {
  auto: "Theme: system",
  light: "Theme: light",
  dark: "Theme: dark",
};

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

export default function Sidebar() {
  const meta = useApp((s) => s.meta);
  const sse = useApp((s) => s.sse);
  const theme = useApp((s) => s.settings?.theme ?? "auto");
  const hasReview = useReview((s) => s.doc !== null);

  const cycleTheme = () => {
    const next = THEME_CYCLE[(THEME_CYCLE.indexOf(theme) + 1) % THEME_CYCLE.length];
    useApp.getState().updateSettings({ theme: next });
  };

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
      </nav>

      <div className="mt-auto flex flex-col items-center gap-2">
        <button
          onClick={cycleTheme}
          title={THEME_LABEL[theme]}
          className="rounded-lg p-2 text-muted transition-colors hover:bg-surface-2 hover:text-fg"
        >
          <Icon d="M12 3a9 9 0 1 0 9 9c0-.5 0-1-.1-1.4A5.5 5.5 0 0 1 12.4 3H12z" />
        </button>
        <div
          title={sse === "open" ? "Connected" : sse === "down" ? "Reconnecting…" : "Connecting…"}
          className={[
            "h-2 w-2 rounded-full",
            sse === "open" ? "bg-emerald-500" : sse === "down" ? "bg-red-400" : "bg-amber-400",
          ].join(" ")}
        />
        <div className="text-[10px] text-muted">{meta ? `v${meta.version}` : ""}</div>
      </div>
    </aside>
  );
}

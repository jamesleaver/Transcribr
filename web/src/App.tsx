import Sidebar from "./components/Sidebar";
import LibraryView from "./views/LibraryView";
import ReviewView from "./views/ReviewView";
import TranscribeView from "./views/TranscribeView";
import { useApp } from "./state/store";

export default function App() {
  const meta = useApp((s) => s.meta);
  const bootError = useApp((s) => s.bootError);
  const view = useApp((s) => s.view);

  if (bootError) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="max-w-md rounded-xl border border-edge bg-surface p-6 text-center">
          <h1 className="mb-2 text-lg font-semibold">Can't reach Transcribr</h1>
          <p className="text-sm text-muted">{bootError}</p>
          <button
            className="mt-4 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-fg"
            onClick={() => void useApp.getState().boot()}
          >
            Try again
          </button>
        </div>
      </div>
    );
  }

  if (!meta) {
    return (
      <div className="flex h-full items-center justify-center text-muted">
        Starting…
      </div>
    );
  }

  return (
    <div className="flex h-full">
      <Sidebar />
      <main className="min-w-0 flex-1 overflow-y-auto">
        {view === "transcribe" && <TranscribeView />}
        {view === "review" && <ReviewView />}
        {view === "library" && <LibraryView />}
      </main>
    </div>
  );
}

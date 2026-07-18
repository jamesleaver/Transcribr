import { useEffect, useRef } from "react";
import AnnotateOverlay from "./components/AnnotateOverlay";
import DialogHost from "./components/DialogHost";
import Sidebar from "./components/Sidebar";
import LibraryView from "./views/LibraryView";
import ModelsView from "./views/ModelsView";
import ReviewView from "./views/ReviewView";
import SettingsView from "./views/SettingsView";
import TranscribeView from "./views/TranscribeView";
import { api } from "./api/client";
import { confirmDialog } from "./state/dialogs";
import { useApp } from "./state/store";
import { useReview, type ReviewPayload } from "./state/reviewStore";

/** On launch: offer to restore a crash-recovery autosave (default Yes,
 *  parity with _maybe_offer_autosave_restore; declining discards). */
function useAutosaveRestoreOffer(ready: boolean) {
  const offered = useRef(false);
  useEffect(() => {
    if (!ready || offered.current) return;
    offered.current = true;
    const snapshot = useApp.getState().snapshot;
    if (!snapshot?.autosave_pending || snapshot.review) return;
    void (async () => {
      const info = await api.get<{ pending: boolean; name?: string }>(
        "/api/autosave",
      );
      if (!info.pending) return;
      const yes = await confirmDialog({
        title: "Restore unsaved review?",
        body:
          "Transcribr found a review session that was never saved " +
          `(probably from a crash or force-quit):\n\n${info.name}\n\n` +
          "Restore it now?",
        confirmLabel: "Restore",
        cancelLabel: "Discard",
        defaultAnswer: true,
      });
      if (!yes) {
        await api.post("/api/autosave/discard");
        return;
      }
      const res = await api.post<{ review: ReviewPayload }>(
        "/api/autosave/restore",
      );
      useReview.getState().openDoc(res.review);
      useApp.getState().setView("review");
    })();
  }, [ready]);
}

export default function App() {
  const meta = useApp((s) => s.meta);
  const bootError = useApp((s) => s.bootError);
  const view = useApp((s) => s.view);
  useAutosaveRestoreOffer(meta !== null);

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
      <main className="min-w-0 flex-1 overflow-x-hidden overflow-y-auto">
        {view === "transcribe" && <TranscribeView />}
        {view === "review" && <ReviewView />}
        {view === "library" && <LibraryView />}
        {view === "models" && <ModelsView />}
        {view === "settings" && <SettingsView />}
      </main>
      <DialogHost />
      {meta.annotate && <AnnotateOverlay />}
    </div>
  );
}

import { onEvent } from "../sse";
import type {
  BatchDone,
  ModelDone,
  ModelJob,
  ModelsPayload,
  Progress,
  RunState,
} from "../api/types";
import { useApp } from "./store";
import { useRun } from "./runStore";
import { useModels } from "./modelsStore";
import { useReview, type ReviewPayload } from "./reviewStore";

// Central registration of SSE event handlers -> store updates.
// Imported once from main.tsx for its side effects.

onEvent("log", (d) => useRun.getState().appendLog((d as { text: string }).text));

onEvent("progress", (d) => useRun.getState().applyProgress(d as Progress));

onEvent("run_state", (d) => useRun.getState().applyRunState(d as RunState));

onEvent("batch_done", (d) => useRun.getState().onBatchDone(d as BatchDone));

onEvent("models", (d) => useModels.getState().applyPayload(d as ModelsPayload));

onEvent("model_progress", (d) =>
  useModels.getState().applyProgress(d as ModelJob | Record<string, never>),
);

onEvent("model_done", (d) => useModels.getState().onDone(d as ModelDone));

onEvent("engines_changed", () => {
  // An engine was installed or removed: refresh the engine roster (so the
  // Transcribe dropdown updates) and the model inventory.
  void useApp.getState().refreshMeta();
  void useModels.getState().refresh();
});

onEvent("files_dropped", (d) => {
  void useRun.getState().addPaths((d as { paths: string[] }).paths);
});

onEvent("recents", () => {
  void useApp.getState().refreshRecents();
});

onEvent("review_opened", (d) => {
  const payload = (d as { review: ReviewPayload }).review;
  useReview.getState().openDoc(payload);
  useApp.getState().setView("review");
});

onEvent("review_closed", () => {
  if (useReview.getState().doc !== null) {
    useReview.getState().closeDoc();
    if (useApp.getState().view === "review") {
      useApp.getState().setView("transcribe");
    }
  }
});

onEvent("audio_status", (d) => {
  useReview.getState().setAudioStatus(d as never);
});

onEvent("review_changed", (d) => {
  const rev = (d as { rev: number }).rev;
  const doc = useReview.getState().doc;
  if (doc && doc.rev !== rev) {
    // Another window mutated the session - resync.
    void useReview.getState().refetch();
  }
});

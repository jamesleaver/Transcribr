import { onEvent } from "../sse";
import type { BatchDone, Progress, RunState } from "../api/types";
import { useApp } from "./store";
import { useRun } from "./runStore";

// Central registration of SSE event handlers -> store updates.
// Imported once from main.tsx for its side effects.

onEvent("log", (d) => useRun.getState().appendLog((d as { text: string }).text));

onEvent("progress", (d) => useRun.getState().applyProgress(d as Progress));

onEvent("run_state", (d) => useRun.getState().applyRunState(d as RunState));

onEvent("batch_done", (d) => useRun.getState().onBatchDone(d as BatchDone));

onEvent("files_dropped", (d) => {
  void useRun.getState().addPaths((d as { paths: string[] }).paths);
});

onEvent("recents", () => {
  void useApp.getState().refreshRecents();
});

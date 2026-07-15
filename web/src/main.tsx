import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { api, initToken } from "./api/client";
import { connectEvents } from "./sse";
import { useApp } from "./state/store";
import { useRun } from "./state/runStore";
import { useReview } from "./state/reviewStore";
import "./state/wiring";
import "./styles/index.css";

initToken();
void useApp.getState().boot();
connectEvents();

// Handy for debugging a local app: poke state from the console.
Object.assign(window as object, { __stores: { useApp, useRun, useReview } });

// Crash-log parity with the Tk app: uncaught front-end errors land in
// transcribr.log via the backend.
window.onerror = (message, source, line, _col, err) => {
  void api
    .post("/api/client-error", {
      message: `${String(message)} at ${source}:${line}`,
      stack: err?.stack ?? "",
    })
    .catch(() => {});
};
window.onunhandledrejection = (e) => {
  void api
    .post("/api/client-error", {
      message: `unhandled rejection: ${String(e.reason)}`,
      stack: e.reason?.stack ?? "",
    })
    .catch(() => {});
};

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { initToken } from "./api/client";
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

window.onerror = (message, source, line) => {
  // Crash-log parity with the Tk app: uncaught front-end errors land in
  // transcribr.log via the backend (endpoint arrives with P2; until
  // then the console is the record).
  console.error(`[transcribr] ${String(message)} at ${source}:${line}`);
};

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

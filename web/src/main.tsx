import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { initToken } from "./api/client";
import { connectEvents } from "./sse";
import { useApp } from "./state/store";
import "./styles/index.css";

initToken();
void useApp.getState().boot();
connectEvents();

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

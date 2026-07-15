import { tokenQuery } from "./api/client";
import { useApp } from "./state/store";

// One EventSource for the app's lifetime. The browser reconnects on its
// own (server sends `retry: 2000`); on a `resync` event — the server
// couldn't bridge our Last-Event-ID gap — we refetch the full state.
//
// Later phases register typed handlers here (progress, run_state,
// review_opened, ...); P0 only tracks connection health.

let source: EventSource | null = null;

type Handler = (data: unknown) => void;
const handlers = new Map<string, Handler>();

export function onEvent(event: string, handler: Handler): void {
  handlers.set(event, handler);
  if (source) attach(source, event, handler);
}

function attach(es: EventSource, event: string, handler: Handler): void {
  es.addEventListener(event, (e) => {
    try {
      handler(JSON.parse((e as MessageEvent).data));
    } catch {
      /* malformed event payload — ignore */
    }
  });
}

export function connectEvents(): void {
  if (source) source.close();
  const es = new EventSource(`/api/events?${tokenQuery()}`);
  source = es;

  es.onopen = () => useApp.getState().setSse("open");
  es.onerror = () => useApp.getState().setSse("down");
  es.addEventListener("resync", () => {
    void useApp.getState().boot();
  });
  for (const [event, handler] of handlers) attach(es, event, handler);
}

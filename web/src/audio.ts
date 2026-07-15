import { tokenQuery } from "./api/client";

// One app-wide <audio> element (never mounted; the DOM plays it fine
// detached). Spans come from the server's playback_span port: play
// from span.start and pause at span.end, or run to the file's end when
// span.end is null (the open-ended last paragraph of a loaded
// transcript).

let el: HTMLAudioElement | null = null;
let stopAt: number | null = null;
let onStop: (() => void) | null = null;

function ensure(): HTMLAudioElement {
  if (el) return el;
  el = new Audio();
  el.preload = "auto";
  el.addEventListener("timeupdate", () => {
    if (el && stopAt !== null && el.currentTime >= stopAt) {
      el.pause();
    }
  });
  el.addEventListener("pause", () => {
    stopAt = null;
    onStop?.();
  });
  el.addEventListener("error", () => {
    stopAt = null;
    onStop?.();
  });
  return el;
}

export function playSpan(
  url: string,
  span: { start: number; end: number | null },
  onStopped: () => void,
): void {
  const audio = ensure();
  const src = `${url}?${tokenQuery()}`;
  onStop = onStopped;
  const begin = () => {
    audio.currentTime = span.start;
    stopAt = span.end;
    void audio.play().catch(() => onStopped());
  };
  if (audio.src.endsWith(src) || audio.src.includes(src)) {
    begin();
  } else {
    audio.src = src;
    audio.addEventListener("loadedmetadata", begin, { once: true });
    audio.load();
  }
}

export function stopPlayback(): void {
  if (el && !el.paused) el.pause();
  stopAt = null;
}

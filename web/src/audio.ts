import { tokenQuery } from "./api/client";

// One app-wide <audio> element (never mounted; the DOM plays it fine
// detached). Spans come from the server's playback_span port: play
// from span.start and pause at span.end, or run on to the file's end
// when span.end is null ("play from here").
//
// Playback is tracked as an explicit session object rather than bare
// module state: an earlier version cleared its stop marker from the
// element's `pause` event, and a queued pause event from a *previous*
// span could wipe the new span's marker - the audio then sailed past
// the paragraph and played to the end of the file with the UI showing
// "stopped". Only the active session can finish itself now.

interface Session {
  stopAt: number | null;
  cb: () => void;
  onTick?: (currentSec: number) => void;
}

let el: HTMLAudioElement | null = null;
let active: Session | null = null;
let selfPause = false;
let playbackRate = 1;

/** Set the playback speed (e.g. 0.5 for half speed). Applies to the
 *  current playback immediately and to every span played afterwards. */
export function setPlaybackRate(rate: number): void {
  playbackRate = rate;
  if (el) el.playbackRate = rate;
}

function finish(): void {
  const s = active;
  active = null;
  if (el && !el.paused) {
    selfPause = true;
    el.pause();
  }
  s?.cb();
}

function ensure(): HTMLAudioElement {
  if (el) return el;
  el = new Audio();
  el.preload = "auto";
  el.addEventListener("timeupdate", () => {
    if (!el || !active) return;
    active.onTick?.(el.currentTime);
    if (active.stopAt !== null && el.currentTime >= active.stopAt) {
      finish();
    }
  });
  el.addEventListener("ended", finish);
  el.addEventListener("error", finish);
  el.addEventListener("pause", () => {
    // Our own pauses are already handled in finish(); this catches
    // external pauses only (OS media keys etc.).
    if (selfPause) {
      selfPause = false;
      return;
    }
    if (active) finish();
  });
  return el;
}

export function playSpan(
  url: string,
  span: { start: number; end: number | null },
  onStopped: () => void,
  onTick?: (currentSec: number) => void,
): void {
  const audio = ensure();
  // The server varies the URL per review session (?v=...), so the
  // same-src reuse below can never replay a previous session's file.
  const src = `${url}${url.includes("?") ? "&" : "?"}${tokenQuery()}`;
  finish();                       // stop + notify any previous span
  const session: Session = { stopAt: span.end, cb: onStopped, onTick };
  active = session;
  const begin = () => {
    if (active !== session) return;   // superseded while loading
    audio.currentTime = span.start;
    audio.playbackRate = playbackRate;
    void audio.play().catch(() => {
      if (active === session) finish();
    });
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
  finish();
}

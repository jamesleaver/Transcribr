import { useState } from "react";
import { useUpdate } from "../state/updateStore";

/** A strip across the top of the app when a newer release exists.
 *  "Update now" downloads the release's installer, checks it against
 *  the checksum GitHub publishes, and hands it to the OS so the user
 *  can watch it run - it may ask for their password. */
export default function UpdateBanner() {
  const status = useUpdate((s) => s.status);
  const dismissed = useUpdate((s) => s.dismissed);
  const [showNotes, setShowNotes] = useState(false);

  if (!status) return null;
  const busy = status.state === "downloading";
  const done = status.state === "ready_to_install";
  const failed = status.state === "error" && status.latest !== null;
  if (!busy && !done && !failed) {
    if (status.state !== "available" || dismissed) return null;
  }

  return (
    <div className="border-b border-edge bg-surface-2 px-4 py-2.5 text-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        {done ? (
          <span>
            <strong>Transcribr {status.latest} is ready to install.</strong>{" "}
            The installer has opened in a new window — follow it through,
            then restart Transcribr.
          </span>
        ) : busy ? (
          <span>
            Downloading Transcribr {status.latest}… {status.pct}%
          </span>
        ) : failed ? (
          <span className="text-red-500">{status.error}</span>
        ) : (
          <>
            <span>
              <strong>Transcribr {status.latest} is available.</strong>{" "}
              You're on {status.current}.
            </span>
            {status.can_install ? (
              <button
                className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg"
                onClick={() => void useUpdate.getState().install()}
              >
                Update now
              </button>
            ) : (
              <a
                className="rounded-lg border border-edge px-3 py-1.5 text-xs font-medium"
                href={status.url}
                target="_blank"
                rel="noreferrer"
              >
                Open the download page
              </a>
            )}
            {status.notes && (
              <button
                className="text-xs text-muted underline"
                onClick={() => setShowNotes((v) => !v)}
              >
                {showNotes ? "Hide what's new" : "What's new"}
              </button>
            )}
            <button
              className="ml-auto text-xs text-muted"
              onClick={() => useUpdate.getState().dismiss()}
            >
              Not now
            </button>
          </>
        )}
        {busy && (
          <div className="h-1.5 w-40 overflow-hidden rounded-full bg-edge">
            <div
              className="h-full bg-accent transition-[width]"
              style={{ width: `${status.pct}%` }}
            />
          </div>
        )}
      </div>
      {showNotes && status.notes && (
        <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-xs text-muted">
          {status.notes}
        </pre>
      )}
    </div>
  );
}

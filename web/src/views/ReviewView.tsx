// Placeholder — the review workspace (transcript document, speakers
// panel, find/replace, playback) lands in Phase 3. The sidebar keeps
// this view disabled until a review session exists.

export default function ReviewView() {
  return (
    <div className="flex h-full items-center justify-center text-sm text-muted">
      No transcript is open for review.
    </div>
  );
}

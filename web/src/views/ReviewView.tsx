import ReviewHeader from "../components/review/ReviewHeader";
import ReviewRail from "../components/review/ReviewRail";
import TranscriptDoc from "../components/review/TranscriptDoc";
import { useReviewHotkeys } from "../components/review/useReviewHotkeys";
import { useReview } from "../state/reviewStore";

export default function ReviewView() {
  const hasDoc = useReview((s) => s.doc !== null);
  useReviewHotkeys();

  if (!hasDoc) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted">
        No transcript is open for review — run a transcription with review
        enabled, or open one from the Library.
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <ReviewHeader />
      <div className="flex min-h-0 flex-1 gap-4 p-4">
        <TranscriptDoc />
        <ReviewRail />
      </div>
      <footer className="border-t border-edge px-6 py-2 text-center text-[11px] text-muted">
        1–9 speaker · 0 clear · M merge · N next attention · double-click
        splits · Enter edit · Esc cancel · ⌘Z undo · ⌘F find
      </footer>
    </div>
  );
}

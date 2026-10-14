import { useEffect, useState } from "react";
import OptionsPanel from "../components/OptionsPanel";
import RunBar from "../components/RunBar";
import RunStrip from "../components/RunStrip";
import SourcePanel from "../components/SourcePanel";
import ReviewHeader from "../components/review/ReviewHeader";
import ReviewRail from "../components/review/ReviewRail";
import TranscriptDoc from "../components/review/TranscriptDoc";
import { useReviewHotkeys } from "../components/review/useReviewHotkeys";
import { useReview } from "../state/reviewStore";

// One surface. Before a run it is the setup page; from the moment a run
// starts it is the document, filling in as the engine works, with the
// run reduced to a single line above it. The review tools are on a rail
// that opens when they are wanted rather than sitting beside the text
// the whole time.

export default function WorkspaceView() {
  const hasDoc = useReview((s) => s.doc !== null);
  const [railOpen, setRailOpen] = useState(true);
  // A re-transcription can be started from a paragraph's right-click
  // menu with the rail shut; open it so its progress can be seen.
  const retransRunning = useReview((s) => s.retrans.running);
  useEffect(() => {
    if (retransRunning) setRailOpen(true);
  }, [retransRunning]);
  useReviewHotkeys();

  if (!hasDoc) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex-1 overflow-y-auto px-6 py-8">
          <div className="mx-auto grid max-w-5xl grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <div>
              <h1 className="mb-4 text-2xl font-semibold">Transcribe</h1>
              <SourcePanel />
            </div>
            <div className="lg:pt-12">
              <OptionsPanel />
            </div>
          </div>
        </div>
        <RunBar />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <RunStrip />
      <ReviewHeader onToggleTools={() => setRailOpen((v) => !v)}
        toolsOpen={railOpen} />
      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-y-auto">
          <TranscriptDoc />
        </div>
        {railOpen && (
          <div className="w-80 shrink-0 overflow-y-auto border-l border-edge p-4">
            <ReviewRail />
          </div>
        )}
      </div>
    </div>
  );
}

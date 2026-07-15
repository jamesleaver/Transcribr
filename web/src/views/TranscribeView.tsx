import OptionsPanel from "../components/OptionsPanel";
import RunBar from "../components/RunBar";
import SourcePanel from "../components/SourcePanel";

export default function TranscribeView() {
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

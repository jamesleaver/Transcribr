"""How many speakers a transcript distinguishes, what they are
labelled, and what colour each one is."""

from .palettes import _PALETTES


def _export_speaker_colour(slot):
    """The review pane's fill for speaker `slot`, as "#rrggbb", or None
    for an unlabelled paragraph.

    Always the light palette, whatever the app theme is set to. The
    review pane is a screen and follows the user's eyes; a transcript
    is a document that will be printed, e-filed and read by people who
    never opened Transcribr, and the dark palette's dimmed fills go to
    mud on white paper."""
    if not slot:
        return None
    return _PALETTES["light"]["speaker_colours"].get(str(slot))



# Sentinel speaker label used in saved transcripts to mark a paragraph
# that explicitly has no speaker assigned, so that loading the file back
# distinguishes "unlabelled by intent" from "continuing the previous
# speaker". Only emitted on a transition from a named speaker to an
# unattributed paragraph; runs of unattributed paragraphs at the start of
# a transcript don't need the marker.
UNATTRIBUTED_LABEL = "[Unattributed]"

# How many speakers a transcript can distinguish, and the slot labels
# they are held under. Nine because that is how many keys 1-9 there are
# to press in the review pane - and, not by coincidence, how many fills
# the palettes above define. Both the speaker detector and the
# transcript model work to this limit, so it lives here rather than
# inside either of them.
MAX_SPEAKERS = 9
SPEAKER_SLOTS = tuple(str(i) for i in range(1, MAX_SPEAKERS + 1))

"""The light and dark colour palettes.

These are the single source of the transcript-semantic colours: the
front-end is served them as CSS variables, and the .docx and .pdf
writers shade speakers with the light set."""


# The colour palettes below are the single source served to the web
# front-end as CSS variables. (The Tk-era theme packages sv-ttk and
# darkdetect are no longer used - the front-end resolves light/dark
# itself, including "auto" via prefers-color-scheme.)


#
# ttk widgets are restyled wholesale by sv-ttk, but plain tk widgets (the
# transcript Text, the prompt/log text boxes, the batch Listbox, speaker
# badges, the drop zone) need explicit colours. Everything below reads
# the active palette via _palette() so a theme switch can re-skin live.

_PALETTES = {
    "light": {
        "text_bg": "#ffffff", "text_fg": "#111111", "insert": "#111111",
        "speaker_fg": "#222222", "timestamp_fg": "#777777",
        "selected_bg": "#d6d6d6", "editing_bg": "#ffcc80",
        "search_bg": "#ffe066",
        "conf_low": "#ffd2d2", "conf_med": "#ffe9c7",
        "badge_fg": "#111111",
        "speaker_colours": {
            "1": "#fff4d6",   # warm yellow
            "2": "#dfeeff",   # soft blue
            "3": "#e3f4d8",   # soft green
            "4": "#f9d9e7",   # soft pink
            "5": "#e7ddf6",   # lavender
            "6": "#ffe0c2",   # peach
            "7": "#d3f0ee",   # teal
            "8": "#f0e4c8",   # tan
            "9": "#d9e8c0",   # olive
        },
        "drop_bg": "#f5f6f8", "drop_hover": "#e8f0fe",
        "drop_border": "#9aa0a6", "drop_fg": "#5f6368",
    },
    "dark": {
        "text_bg": "#1e1e20", "text_fg": "#e6e6e6", "insert": "#e6e6e6",
        "speaker_fg": "#dddddd", "timestamp_fg": "#9a9a9a",
        "selected_bg": "#46484c", "editing_bg": "#6b4e1e",
        "search_bg": "#7a6a14",
        "conf_low": "#5c2b2b", "conf_med": "#5c4a26",
        "badge_fg": "#e6e6e6",
        "speaker_colours": {
            "1": "#4d4426",   # warm yellow, dimmed
            "2": "#283d52",   # soft blue, dimmed
            "3": "#2f4528",   # soft green, dimmed
            "4": "#4d2c3c",   # soft pink, dimmed
            "5": "#3b3050",   # lavender, dimmed
            "6": "#52391f",   # peach, dimmed
            "7": "#1f4441",   # teal, dimmed
            "8": "#46402a",   # tan, dimmed
            "9": "#3a4426",   # olive, dimmed
        },
        "drop_bg": "#2a2a2c", "drop_hover": "#1f3a5f",
        "drop_border": "#5f6368", "drop_fg": "#9aa0a6",
    },
}

# The currently-active palette key. Set by _apply_theme(); defaults to
# light so module-level consumers work before the GUI configures a theme.
_ACTIVE_THEME = "light"


def _resolve_theme(setting):
    """Map a theme setting ("auto"/"light"/"dark") to a palette key. The
    running app resolves "auto" in the front-end (prefers-color-scheme);
    server-side it falls back to light."""
    if setting in ("light", "dark"):
        return setting
    return "light"


def _palette():
    return _PALETTES[_ACTIVE_THEME]

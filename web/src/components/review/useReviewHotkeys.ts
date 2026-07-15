import { useEffect } from "react";
import { useReview } from "../../state/reviewStore";

// The exact Tk key bindings, active only while the review view is
// mounted. Guard ported from _is_text_input_focused: when a name/find
// field (or the edit textarea) has focus, only Cmd/Ctrl+F passes;
// everything else stays native.

function isTextInputFocused(): boolean {
  const el = document.activeElement;
  if (!el) return false;
  const tag = el.tagName;
  return (
    tag === "INPUT" ||
    tag === "TEXTAREA" ||
    tag === "SELECT" ||
    (el as HTMLElement).isContentEditable
  );
}

export function useReviewHotkeys(): void {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const st = useReview.getState();
      if (!st.doc) return;
      const mod = e.metaKey || e.ctrlKey;

      if (mod && (e.key === "f" || e.key === "F")) {
        e.preventDefault();
        const find = document.getElementById("review-find") as HTMLInputElement | null;
        find?.focus();
        find?.select();
        return;
      }

      if (isTextInputFocused()) return;

      if (mod && (e.key === "z" || e.key === "Z")) {
        e.preventDefault();
        void (e.shiftKey ? st.redo() : st.undo());
        return;
      }
      if (mod && (e.key === "y" || e.key === "Y")) {
        e.preventDefault();
        void st.redo();
        return;
      }
      if (mod) return;

      switch (e.key) {
        case "1": case "2": case "3": case "4": case "5":
        case "6": case "7": case "8": case "9":
          e.preventDefault();
          void st.setSpeaker(st.selected, e.key);
          return;
        case "0":
          e.preventDefault();
          void st.setSpeaker(st.selected, null);
          return;
        case "m": case "M":
          e.preventDefault();
          void st.merge(st.selected);
          return;
        case "n": case "N":
          e.preventDefault();
          st.jumpNextAttention();
          return;
        case "p": case "P":
          e.preventDefault();
          st.togglePlay(st.selected);
          return;
        case "Enter": case "F2":
          e.preventDefault();
          st.startEdit(st.selected);
          return;
        case "ArrowUp":
          e.preventDefault();
          st.select(st.selected - 1);
          return;
        case "ArrowDown":
          e.preventDefault();
          st.select(st.selected + 1);
          return;
        default:
          return;
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
}

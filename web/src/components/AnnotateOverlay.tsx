/* Developer annotation overlay — ported from the Chambers Availability
   tool's DevTools. Mounts only when the backend was started with
   --annotate (or TRANSCRIBR_ANNOTATE=1); invisible otherwise.

   A floating ✎ toggle turns on annotate mode: hovering highlights the
   element under the cursor and a click captures it — view, selector
   path, visible text, geometry, a markup snippet — and opens a note
   box. Saved notes land in annotations.json under the config dir; the
   📋 button reviews them and "Copy for Claude" formats the lot as
   markdown for a development session.

   Everything is styled INLINE so the overlay survives a stale-CSS
   partial update. Clicks are intercepted in the CAPTURE phase while
   annotate mode is on, so annotating a button never presses it. */
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useApp } from "../state/store";

const Z = 400; // above every app popover

interface Draft {
  selector: string;
  elementText: string;
  html: string;
  rect: string;
  x: number;
  y: number;
}

interface Annotation {
  id: number;
  created: string;
  app_version: string;
  view: string;
  selector: string;
  element_text: string;
  html: string;
  rect: string;
  note: string;
}

/** CSS-ish selector path: nearest id wins, else tag + classes (+ nth-of-type
    when siblings share the shape), capped at 6 hops — enough to find the
    element in the source, not a brittle full path. */
export function cssPath(el: Element): string {
  const parts: string[] = [];
  let cur: Element | null = el;
  while (cur && cur.tagName !== "BODY" && cur.tagName !== "HTML" && parts.length < 6) {
    if (cur.id) {
      parts.unshift(`#${cur.id}`);
      break;
    }
    let seg = cur.tagName.toLowerCase();
    const classes = Array.from(cur.classList).slice(0, 2);
    if (classes.length) seg += "." + classes.join(".");
    const parent: Element | null = cur.parentElement;
    if (parent) {
      const same = Array.from(parent.children).filter(
        (c) => c.tagName === cur!.tagName && c.className === (cur as HTMLElement).className,
      );
      if (same.length > 1) seg += `:nth-of-type(${same.indexOf(cur) + 1})`;
    }
    parts.unshift(seg);
    cur = parent;
  }
  return parts.join(" > ");
}

function exportMarkdown(items: Annotation[]): string {
  const lines = [`# Transcribr annotations (${items.length})`, ""];
  for (const a of items) {
    lines.push(`## #${a.id} · ${a.view} view · ${a.created} · v${a.app_version}`);
    lines.push(`- Element: \`${a.selector || "(unnamed)"}\``);
    if (a.element_text) lines.push(`- Text: "${a.element_text.slice(0, 120)}"`);
    lines.push(`- Geometry: ${a.rect}`);
    lines.push(`- Note: ${a.note}`);
    if (a.html) lines.push("", "```html", a.html, "```");
    lines.push("");
  }
  return lines.join("\n");
}

export default function AnnotateOverlay() {
  const view = useApp((s) => s.view);
  const [picking, setPicking] = useState(false);
  const [hover, setHover] = useState<DOMRect | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [note, setNote] = useState("");
  const [flash, setFlash] = useState("");
  const [panel, setPanel] = useState(false);
  const [items, setItems] = useState<Annotation[]>([]);
  const [file, setFile] = useState("");
  const pickingRef = useRef(false);
  pickingRef.current = picking && !draft;

  const say = (msg: string, ms = 2500) => {
    setFlash(msg);
    setTimeout(() => setFlash(""), ms);
  };

  const refresh = useCallback(async () => {
    const res = await api.get<{ items: Annotation[]; file: string }>("/api/annotations");
    setItems(res.items);
    setFile(res.file);
  }, []);

  useEffect(() => {
    if (panel) void refresh();
  }, [panel, refresh]);

  const capture = useCallback((el: Element, x: number, y: number) => {
    const r = el.getBoundingClientRect();
    const rect = JSON.stringify({
      x: Math.round(r.x),
      y: Math.round(r.y),
      w: Math.round(r.width),
      h: Math.round(r.height),
      vw: window.innerWidth,
      vh: window.innerHeight,
    });
    setDraft({
      selector: cssPath(el),
      elementText: ((el as HTMLElement).innerText || el.textContent || "").trim().slice(0, 500),
      html: el.outerHTML.slice(0, 4000),
      rect,
      x: Math.min(x, window.innerWidth - 340),
      y: Math.min(y, window.innerHeight - 240),
    });
    setNote("");
  }, []);

  // annotate mode: highlight on hover, capture on click — all in the
  // capture phase so the page underneath never reacts
  useEffect(() => {
    if (!picking) return;
    const overDevUi = (t: EventTarget | null) =>
      t instanceof Element && !!t.closest("[data-dev-ui]");
    const onMove = (e: MouseEvent) => {
      if (!pickingRef.current || overDevUi(e.target)) {
        setHover(null);
        return;
      }
      const el = e.target instanceof Element ? e.target : null;
      setHover(el ? el.getBoundingClientRect() : null);
    };
    const onDown = (e: MouseEvent) => {
      if (!pickingRef.current || overDevUi(e.target)) return;
      e.preventDefault();
      e.stopPropagation();
    };
    const onClick = (e: MouseEvent) => {
      if (!pickingRef.current || overDevUi(e.target)) return;
      e.preventDefault();
      e.stopPropagation();
      if (e.target instanceof Element) capture(e.target, e.clientX, e.clientY);
      setHover(null);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setDraft(null);
        setPicking(false);
        setHover(null);
      }
    };
    document.addEventListener("mousemove", onMove, true);
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("mousedown", onDown, true);
    document.addEventListener("mouseup", onDown, true);
    document.addEventListener("click", onClick, true);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousemove", onMove, true);
      document.removeEventListener("pointerdown", onDown, true);
      document.removeEventListener("mousedown", onDown, true);
      document.removeEventListener("mouseup", onDown, true);
      document.removeEventListener("click", onClick, true);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [picking, capture]);

  async function save() {
    if (!draft || !note.trim()) return;
    try {
      await api.post("/api/annotations", {
        view,
        selector: draft.selector,
        element_text: draft.elementText,
        html: draft.html,
        rect: draft.rect,
        note,
      });
      setDraft(null);
      say("Annotation saved — 📋 to review");
    } catch (e) {
      say(String((e as Error).message || e), 4000);
    }
  }

  async function copyAll() {
    const text = exportMarkdown(items);
    try {
      await navigator.clipboard.writeText(text);
      say("Copied for Claude");
    } catch {
      // WKWebView can refuse programmatic clipboard access; fall back.
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
      say("Copied for Claude");
    }
  }

  const btn: React.CSSProperties = {
    border: "1px solid #c9a45c",
    background: "none",
    color: "#c9a45c",
    cursor: "pointer",
    fontSize: 12,
    padding: "3px 10px",
    fontFamily: "inherit",
  };
  const fab: React.CSSProperties = {
    position: "fixed",
    right: 14,
    zIndex: Z,
    width: 40,
    height: 40,
    border: "1px solid #c9a45c",
    background: "#17222c",
    color: "#c9a45c",
    fontSize: 18,
    cursor: "pointer",
    boxShadow: "0 4px 14px rgba(23,34,44,.35)",
  };

  return (
    <div data-dev-ui>
      {/* toggles */}
      <button
        onClick={() => {
          setPicking(!picking);
          setDraft(null);
          setHover(null);
          setPanel(false);
        }}
        title={
          picking
            ? "Annotate mode ON — click any element to pin a note; Esc to stop"
            : "Annotate mode: pin a note to any element on the page"
        }
        style={{
          ...fab,
          bottom: 14,
          background: picking ? "#c9a45c" : "#17222c",
          color: picking ? "#17222c" : "#c9a45c",
        }}
      >
        ✎
      </button>
      <button
        onClick={() => {
          setPanel(!panel);
          setPicking(false);
          setDraft(null);
        }}
        title="Review saved annotations"
        style={{ ...fab, bottom: 60, fontSize: 15 }}
      >
        📋
      </button>

      {picking && !draft && (
        <div
          style={{
            position: "fixed",
            bottom: 106,
            right: 14,
            zIndex: Z,
            background: "#17222c",
            color: "#f4efe6",
            fontSize: 11,
            padding: "4px 10px",
            border: "1px solid #c9a45c",
          }}
        >
          Click the element your note is about · Esc to stop
        </div>
      )}

      {/* hover highlight */}
      {picking && !draft && hover && (
        <div
          style={{
            position: "fixed",
            left: hover.x - 2,
            top: hover.y - 2,
            width: hover.width + 4,
            height: hover.height + 4,
            zIndex: Z - 1,
            border: "2px solid #c9a45c",
            background: "rgba(201,164,92,.12)",
            pointerEvents: "none",
          }}
        />
      )}

      {/* note box */}
      {draft && (
        <div
          style={{
            position: "fixed",
            left: Math.max(8, draft.x),
            top: Math.max(8, draft.y),
            zIndex: Z,
            width: 320,
            background: "#ffffff",
            border: "1px solid #17222c",
            boxShadow: "0 8px 30px rgba(23,34,44,.35)",
            padding: 12,
            fontSize: 12,
            color: "#17222c",
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Annotate this element</div>
          <div
            style={{
              fontFamily: "monospace",
              fontSize: 10,
              color: "#5c6670",
              marginBottom: 8,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            title={draft.selector}
          >
            {draft.selector || "(unnamed element)"}
          </div>
          <textarea
            autoFocus
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="What should change here?"
            rows={4}
            style={{
              width: "100%",
              boxSizing: "border-box",
              border: "1px solid #b9bfc6",
              padding: 6,
              fontFamily: "inherit",
              fontSize: 12,
              resize: "vertical",
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void save();
            }}
          />
          <div style={{ display: "flex", gap: 8, marginTop: 8, justifyContent: "flex-end" }}>
            <button
              style={{ ...btn, color: "#5c6670", borderColor: "#b9bfc6" }}
              onClick={() => setDraft(null)}
            >
              Cancel
            </button>
            <button
              style={{ ...btn, background: "#17222c", opacity: note.trim() ? 1 : 0.5 }}
              onClick={() => void save()}
              disabled={!note.trim()}
              title="Save the note (⌘↵)"
            >
              Save note
            </button>
          </div>
        </div>
      )}

      {/* review panel */}
      {panel && (
        <div
          style={{
            position: "fixed",
            bottom: 106,
            right: 14,
            zIndex: Z,
            width: 380,
            maxHeight: "60vh",
            display: "flex",
            flexDirection: "column",
            background: "#ffffff",
            border: "1px solid #17222c",
            boxShadow: "0 8px 30px rgba(23,34,44,.35)",
            fontSize: 12,
            color: "#17222c",
          }}
        >
          <div
            style={{
              padding: "8px 12px",
              borderBottom: "1px solid #d9dde2",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <b>Annotations ({items.length})</b>
            <div style={{ display: "flex", gap: 8 }}>
              <button style={{ ...btn, color: "#17222c", borderColor: "#17222c" }}
                onClick={() => void copyAll()} disabled={!items.length}>
                Copy for Claude
              </button>
              <button
                style={{ ...btn, color: "#8a2f2f", borderColor: "#8a2f2f" }}
                onClick={async () => {
                  await api.post("/api/annotations/clear");
                  void refresh();
                }}
                disabled={!items.length}
              >
                Clear all
              </button>
            </div>
          </div>
          <div style={{ overflowY: "auto", padding: "4px 0" }}>
            {!items.length && (
              <div style={{ padding: 12, color: "#5c6670" }}>
                Nothing yet — use ✎ to pin a note to any element.
              </div>
            )}
            {items.map((a) => (
              <div
                key={a.id}
                style={{ padding: "8px 12px", borderBottom: "1px solid #eef0f2" }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                  <span style={{ color: "#5c6670" }}>
                    #{a.id} · {a.view} · {a.created}
                  </span>
                  <button
                    style={{ ...btn, padding: "0 6px", color: "#8a2f2f", borderColor: "transparent" }}
                    title="Delete this annotation"
                    onClick={async () => {
                      await api.post("/api/annotations/delete", { id: a.id });
                      void refresh();
                    }}
                  >
                    ✕
                  </button>
                </div>
                <div style={{ margin: "2px 0 4px" }}>{a.note}</div>
                <div
                  style={{
                    fontFamily: "monospace",
                    fontSize: 10,
                    color: "#5c6670",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                  title={a.selector}
                >
                  {a.selector}
                </div>
              </div>
            ))}
          </div>
          <div
            style={{
              padding: "6px 12px",
              borderTop: "1px solid #d9dde2",
              color: "#5c6670",
              fontSize: 10,
              fontFamily: "monospace",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            title={file}
          >
            {file}
          </div>
        </div>
      )}

      {/* flash */}
      {flash && (
        <div
          style={{
            position: "fixed",
            bottom: 152,
            right: 14,
            zIndex: Z,
            background: "#2f5d3a",
            color: "#fff",
            fontSize: 12,
            padding: "6px 12px",
            boxShadow: "0 4px 14px rgba(23,34,44,.35)",
          }}
        >
          {flash}
        </div>
      )}
    </div>
  );
}

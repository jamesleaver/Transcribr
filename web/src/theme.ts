import type { Meta, Palette, ThemeSetting } from "./api/types";

// Python's _PALETTES stays the single source of transcript colours; we
// inject both palettes as CSS custom properties and let data-theme on
// <html> decide which set applies. "auto" follows prefers-color-scheme,
// matching the Tk app's darkdetect behaviour.

function paletteVars(p: Palette): string {
  const lines: string[] = [];
  for (const [key, value] of Object.entries(p)) {
    if (key === "speaker_colours") {
      for (const [slot, colour] of Object.entries(value as Record<string, string>)) {
        lines.push(`--speaker-${slot}: ${colour};`);
      }
    } else {
      lines.push(`--${key.replace(/_/g, "-")}: ${value};`);
    }
  }
  return lines.join("\n  ");
}

export function injectPalettes(palettes: Meta["palettes"]): void {
  let el = document.getElementById("palette-vars") as HTMLStyleElement | null;
  if (!el) {
    el = document.createElement("style");
    el.id = "palette-vars";
    document.head.appendChild(el);
  }
  el.textContent = [
    `:root, :root[data-theme="light"] {\n  ${paletteVars(palettes.light)}\n}`,
    `:root[data-theme="dark"] {\n  ${paletteVars(palettes.dark)}\n}`,
  ].join("\n");
}

const media = window.matchMedia("(prefers-color-scheme: dark)");
let currentSetting: ThemeSetting = "auto";

function apply(): void {
  const effective =
    currentSetting === "auto" ? (media.matches ? "dark" : "light") : currentSetting;
  document.documentElement.dataset.theme = effective;
}

export function setTheme(setting: ThemeSetting): void {
  currentSetting = setting;
  apply();
}

media.addEventListener("change", apply);

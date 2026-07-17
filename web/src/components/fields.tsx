import type { ReactNode } from "react";

// Small shared form primitives for the options cards.

export const inputCls =
  "rounded-lg border border-edge bg-surface px-3 py-2 text-sm text-fg " +
  "focus:border-accent focus:outline-none disabled:opacity-40";

export function Field({
  label,
  note,
  children,
}: {
  label: string;
  note?: string;
  children: ReactNode;
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1.5">
      <span className="text-xs font-medium tracking-wide text-muted">{label}</span>
      {children}
      {note && <span className="text-xs text-muted">{note}</span>}
    </label>
  );
}

export function SelectField({
  label,
  value,
  options,
  onChange,
  note,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
  note?: string;
}) {
  return (
    <Field label={label} note={note}>
      <select className={inputCls} value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </Field>
  );
}

export function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step,
  note,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  note?: string;
}) {
  return (
    <Field label={label} note={note}>
      <input
        type="number"
        className={inputCls}
        value={value}
        min={min}
        max={max}
        step={step ?? 1}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (Number.isFinite(v)) onChange(v);
        }}
      />
    </Field>
  );
}

export function CheckField({
  label,
  checked,
  onChange,
  disabled,
  note,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean;
  note?: string;
}) {
  return (
    <label
      className={`flex items-start gap-2.5 text-sm ${disabled ? "opacity-50" : "cursor-pointer"}`}
    >
      <input
        type="checkbox"
        className="mt-0.5 h-4 w-4 accent-(--accent)"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>
        {label}
        {note && <span className="block text-xs text-muted">{note}</span>}
      </span>
    </label>
  );
}

export function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-edge bg-surface p-5">
      <h2 className="mb-4 text-sm font-semibold">{title}</h2>
      {children}
    </section>
  );
}

export function Disclosure({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <details className="group rounded-xl border border-edge bg-surface">
      <summary className="cursor-pointer select-none px-5 py-3.5 text-sm font-semibold marker:content-none">
        <span className="mr-2 inline-block transition-transform group-open:rotate-90">▸</span>
        {title}
      </summary>
      <div className="border-t border-edge p-5">{children}</div>
    </details>
  );
}

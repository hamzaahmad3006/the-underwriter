import type { InputHTMLAttributes } from 'react'

export function Input({
  label,
  numeric,
  className = '',
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string; numeric?: boolean }) {
  return (
    <label className="block">
      <span className="text-muted text-[11px] tracking-wide uppercase">{label}</span>
      <input
        {...props}
        className={`border-line bg-surface text-ink focus:border-accent mt-1 w-full rounded border px-2 py-1.5 text-sm outline-none ${
          numeric ? 'num text-right' : ''
        } ${className}`}
      />
    </label>
  )
}

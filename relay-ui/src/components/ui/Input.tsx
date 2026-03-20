import type { InputHTMLAttributes } from 'react';

interface Props extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  badge?: string;
  badgeColor?: string;
}

export function Input({ label, badge, badgeColor = 'text-accent', className = '', ...props }: Props) {
  return (
    <div className="flex flex-col gap-1.5">
      {label && (
        <div className="flex items-center gap-2">
          <label className="text-xs font-medium text-fg-muted">{label}</label>
          {badge && <span className={`text-[0.625rem] font-semibold ${badgeColor}`}>{badge}</span>}
        </div>
      )}
      <input
        className={`
          bg-bg-input border border-border text-fg text-sm
          px-3 py-2.5 rounded-[var(--radius)]
          outline-none transition-all duration-200
          focus:border-accent/50 focus:shadow-[0_0_0_3px_var(--color-accent-muted)]
          placeholder:text-fg-faint
          ${className}
        `}
        {...props}
      />
    </div>
  );
}

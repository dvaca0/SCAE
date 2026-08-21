/**
 * Interface primitives.
 *
 * Everything the platform renders is built from these, so density, spacing
 * and colour stay consistent across the admin console and the guard station.
 */
import { type ReactNode, forwardRef } from 'react'
import { Loader2, type LucideIcon } from 'lucide-react'

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ')
}

/* --- Card ---------------------------------------------------------------- */
export function Card({
  children,
  className,
  padded = true,
}: {
  children: ReactNode
  className?: string
  padded?: boolean
}) {
  return (
    <section className={cx('surface-card', padded && 'p-5', className)}>{children}</section>
  )
}

export function CardHeader({
  title,
  subtitle,
  icon: Icon,
  action,
}: {
  title: string
  subtitle?: string
  icon?: LucideIcon
  action?: ReactNode
}) {
  return (
    <header className="mb-4 flex items-start justify-between gap-4">
      <div className="flex items-start gap-3">
        {Icon && (
          <span
            className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg"
            style={{ background: 'var(--surface-sunken)', color: 'var(--text-muted)' }}
          >
            <Icon size={16} strokeWidth={2} />
          </span>
        )}
        <div>
          <h2 className="text-[15px] font-semibold tracking-tight">{title}</h2>
          {subtitle && (
            <p className="mt-0.5 text-[13px]" style={{ color: 'var(--text-muted)' }}>
              {subtitle}
            </p>
          )}
        </div>
      </div>
      {action}
    </header>
  )
}

/* --- Button -------------------------------------------------------------- */
type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'success'
  size?: 'sm' | 'md' | 'lg'
  icon?: LucideIcon
  loading?: boolean
  full?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', icon: Icon, loading, full, children, className, disabled, ...rest },
  ref,
) {
  const sizes = {
    sm: 'h-8 px-3 text-[13px] gap-1.5',
    md: 'h-9 px-3.5 text-[13px] gap-2',
    lg: 'h-12 px-5 text-[15px] gap-2.5',
  }
  const variants: Record<string, string> = {
    primary: 'bg-uc-600 text-white hover:bg-uc-700 active:bg-uc-800 border-transparent shadow-sm',
    secondary: 'hover:bg-[var(--surface-sunken)] active:bg-[var(--surface-sunken)]',
    ghost: 'border-transparent hover:bg-[var(--surface-sunken)]',
    danger: 'bg-[var(--btn-deny)] text-white hover:bg-[var(--btn-deny-hover)] border-transparent shadow-sm',
    success: 'bg-[var(--btn-allow)] text-white hover:bg-[var(--btn-allow-hover)] border-transparent shadow-sm',
  }
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cx(
        'inline-flex items-center justify-center rounded-lg border font-medium transition-colors',
        'disabled:cursor-not-allowed disabled:opacity-50',
        sizes[size],
        variants[variant],
        variant === 'secondary' && 'bg-[var(--surface-raised)]',
        full && 'w-full',
        className,
      )}
      {...rest}
    >
      {loading ? <Loader2 size={size === 'lg' ? 18 : 14} className="animate-spin" /> : Icon && <Icon size={size === 'lg' ? 18 : 14} />}
      {children}
    </button>
  )
})

/* --- Badges -------------------------------------------------------------- */
const TONES = {
  allow: { fg: 'var(--allow)', bg: 'var(--allow-bg)' },
  deny: { fg: 'var(--deny)', bg: 'var(--deny-bg)' },
  review: { fg: 'var(--review)', bg: 'var(--review-bg)' },
  info: { fg: 'var(--info)', bg: 'var(--info-bg)' },
  neutral: { fg: 'var(--text-muted)', bg: 'var(--surface-sunken)' },
} as const

export type Tone = keyof typeof TONES

export function Badge({
  children,
  tone = 'neutral',
  dot,
  className,
}: {
  children: ReactNode
  tone?: Tone
  dot?: boolean
  className?: string
}) {
  const { fg, bg } = TONES[tone]
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-[12px] font-medium whitespace-nowrap',
        className,
      )}
      style={{ color: fg, background: bg }}
    >
      {dot && <span className="h-1.5 w-1.5 rounded-full" style={{ background: fg }} />}
      {children}
    </span>
  )
}

export function decisionTone(decision: string): Tone {
  if (decision === 'allow') return 'allow'
  if (decision === 'deny') return 'deny'
  return 'review'
}

/** Marks seeded content so demo material is never mistaken for real records. */
export function OriginBadge({ origin }: { origin: string }) {
  if (origin !== 'demo') return null
  return (
    <span
      className="inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold tracking-wide uppercase"
      style={{ color: 'var(--color-gold-700)', background: 'var(--color-gold-100)' }}
      title="Dato de demostración. No corresponde a un registro real."
    >
      Demo
    </span>
  )
}

/* --- Plate --------------------------------------------------------------- */
export function Plate({
  value,
  size = 'md',
  muted,
}: {
  value?: string | null
  size?: 'sm' | 'md' | 'lg' | 'xl'
  muted?: boolean
}) {
  const sizes = {
    sm: 'text-[12px] px-1.5 py-0.5',
    md: 'text-[13px] px-2 py-1',
    lg: 'text-[20px] px-3 py-1.5',
    xl: 'text-[34px] px-5 py-2.5',
  }
  if (!value) {
    return (
      <span
        className={cx('plate inline-block whitespace-nowrap rounded-md border border-dashed', sizes[size])}
        style={{ color: 'var(--text-subtle)' }}
      >
        SIN LECTURA
      </span>
    )
  }
  return (
    <span
      className={cx('plate inline-block whitespace-nowrap rounded-md border', sizes[size])}
      style={{
        background: muted ? 'var(--surface-sunken)' : 'var(--surface-raised)',
        borderColor: 'var(--border-strong)',
      }}
    >
      {value}
    </span>
  )
}

/* --- States -------------------------------------------------------------- */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: LucideIcon
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      <span
        className="mb-3 grid h-11 w-11 place-items-center rounded-xl"
        style={{ background: 'var(--surface-sunken)', color: 'var(--text-subtle)' }}
      >
        <Icon size={20} />
      </span>
      <p className="text-[14px] font-medium">{title}</p>
      {description && (
        <p className="mt-1 max-w-sm text-[13px]" style={{ color: 'var(--text-muted)' }}>
          {description}
        </p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-12" style={{ color: 'var(--text-muted)' }}>
      <Loader2 size={16} className="animate-spin" />
      {label && <span className="text-[13px]">{label}</span>}
    </div>
  )
}

export function SkeletonRows({ rows = 5, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="divide-y" style={{ borderColor: 'var(--border)' }}>
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex gap-4 px-4 py-3">
          {Array.from({ length: cols }).map((_, c) => (
            <div
              key={c}
              className="h-3.5 animate-pulse rounded"
              style={{
                background: 'var(--surface-sunken)',
                width: c === 0 ? '22%' : `${12 + ((r + c) % 3) * 6}%`,
              }}
            />
          ))}
        </div>
      ))}
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="px-6 py-10 text-center">
      <p className="text-[14px] font-medium" style={{ color: 'var(--deny)' }}>
        No se pudo cargar la información
      </p>
      <p className="mt-1 text-[13px]" style={{ color: 'var(--text-muted)' }}>
        {message}
      </p>
      {onRetry && (
        <Button className="mt-4" size="sm" onClick={onRetry}>
          Reintentar
        </Button>
      )}
    </div>
  )
}

/* --- Table --------------------------------------------------------------- */
export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[13px]">{children}</table>
    </div>
  )
}

export function Th({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <th
      className={cx(
        'sticky top-0 z-10 whitespace-nowrap px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider',
        className,
      )}
      style={{ color: 'var(--text-subtle)', background: 'var(--surface-sunken)' }}
    >
      {children}
    </th>
  )
}

export function Td({ children, className }: { children?: ReactNode; className?: string }) {
  return <td className={cx('px-3 py-2.5 align-middle', className)}>{children}</td>
}

export function Tr({
  children,
  onClick,
  active,
}: {
  children: ReactNode
  onClick?: () => void
  active?: boolean
}) {
  return (
    <tr
      onClick={onClick}
      className={cx(
        'border-t transition-colors',
        onClick && 'cursor-pointer hover:bg-[var(--surface-sunken)]',
      )}
      style={{
        borderColor: 'var(--border)',
        background: active ? 'var(--surface-sunken)' : undefined,
      }}
    >
      {children}
    </tr>
  )
}

/* --- Form fields --------------------------------------------------------- */
export function Field({
  label,
  hint,
  error,
  children,
  required,
}: {
  label: string
  hint?: string
  error?: string
  children: ReactNode
  required?: boolean
}) {
  return (
    <label className="block">
      <span className="mb-1.5 flex items-center gap-1 text-[13px] font-medium">
        {label}
        {required && <span style={{ color: 'var(--deny)' }}>*</span>}
      </span>
      {children}
      {error ? (
        <span className="mt-1 block text-[12px]" style={{ color: 'var(--deny)' }}>
          {error}
        </span>
      ) : hint ? (
        <span className="mt-1 block text-[12px]" style={{ color: 'var(--text-subtle)' }}>
          {hint}
        </span>
      ) : null}
    </label>
  )
}

// No width here on purpose: a `w-full` in the base competes with any width
// utility passed by the caller and Tailwind gives no ordering guarantee.
// Callers wrap the control in a sized container instead.
const controlClass =
  'block w-full rounded-lg border px-3 py-2 text-[13px] outline-none transition-colors ' +
  'placeholder:normal-case placeholder:text-[var(--text-subtle)] focus:border-uc-500'

export const Input = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...rest }, ref) {
    return (
      <input
        ref={ref}
        className={cx(controlClass, className)}
        style={{ background: 'var(--surface-raised)' }}
        {...rest}
      />
    )
  },
)

export const Select = forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  function Select({ className, children, ...rest }, ref) {
    return (
      <select
        ref={ref}
        className={cx(controlClass, 'appearance-none pr-8', className)}
        style={{ background: 'var(--surface-raised)' }}
        {...rest}
      >
        {children}
      </select>
    )
  },
)

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className, ...rest }, ref) {
  return (
    <textarea
      ref={ref}
      className={cx(controlClass, 'min-h-[76px] resize-y', className)}
      style={{ background: 'var(--surface-raised)' }}
      {...rest}
    />
  )
})

/* --- Modal --------------------------------------------------------------- */
export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  width = 'md',
}: {
  open: boolean
  onClose: () => void
  title: string
  description?: string
  children: ReactNode
  footer?: ReactNode
  width?: 'sm' | 'md' | 'lg'
}) {
  if (!open) return null
  const widths = { sm: 'max-w-md', md: 'max-w-lg', lg: 'max-w-3xl' }
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-4 pt-[8vh]"
      style={{ background: 'rgb(10 15 13 / 0.55)' }}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        className={cx('animate-in-up w-full rounded-xl border', widths[width])}
        style={{
          background: 'var(--surface-raised)',
          borderColor: 'var(--border)',
          boxShadow: 'var(--shadow-pop)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b px-5 py-4" style={{ borderColor: 'var(--border)' }}>
          <h3 className="text-[15px] font-semibold">{title}</h3>
          {description && (
            <p className="mt-0.5 text-[13px]" style={{ color: 'var(--text-muted)' }}>
              {description}
            </p>
          )}
        </div>
        <div className="px-5 py-4">{children}</div>
        {footer && (
          <div
            className="flex justify-end gap-2 border-t px-5 py-3.5"
            style={{ borderColor: 'var(--border)', background: 'var(--surface-sunken)' }}
          >
            {footer}
          </div>
        )}
      </div>
    </div>
  )
}

/* --- Misc ---------------------------------------------------------------- */
export function Toolbar({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center gap-2">{children}</div>
}

export function Pagination({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number
  pageSize: number
  total: number
  onPage: (p: number) => void
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  if (total === 0) return null
  const from = (page - 1) * pageSize + 1
  const to = Math.min(total, page * pageSize)
  return (
    <div
      className="flex items-center justify-between border-t px-4 py-3 text-[13px]"
      style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
    >
      <span className="num">
        {from}–{to} de {total.toLocaleString('es-MX')}
      </span>
      <div className="flex items-center gap-2">
        <Button size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          Anterior
        </Button>
        <span className="num px-1">
          {page} / {pages}
        </span>
        <Button size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Siguiente
        </Button>
      </div>
    </div>
  )
}

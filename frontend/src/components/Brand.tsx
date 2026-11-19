import { useId } from 'react'

/**

 * SCAE identity.
 *
 * This is an original mark for the platform, not the Universidad de Colima
 * escudo. The official emblem is governed by the university's identity
 * policy and may only be used on official material, so SCAE carries its own
 * symbol and inherits the institution through the palette and typography.
 *
 * The mark reads as a shield (security) whose lower half is a raised barrier
 * over a lane — the single action the whole platform exists to control.
 */
export function Logo({ size = 32 }: { size?: number }) {
  // The mark renders more than once per page (sidebar + mobile header). A
  // hard-coded gradient id would collide, and `url(#id)` resolves to the
  // FIRST match in the document — which may sit inside a `display:none`
  // subtree, leaving the visible copy unpainted. useId keeps them distinct.
  const gradientId = `scae-shield-${useId()}`
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 40 40"
      fill="none"
      role="img"
      aria-label="SCAE"
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--color-uc-500)" />
          <stop offset="100%" stopColor="var(--color-uc-700)" />
        </linearGradient>
      </defs>
      {/* Shield */}
      <path
        d="M20 2.5 5.5 8v12.2C5.5 28.7 11.6 35.4 20 37.5c8.4-2.1 14.5-8.8 14.5-17.3V8L20 2.5Z"
        fill={`url(#${gradientId})`}
      />
      {/* Lane surface */}
      <path d="M9.5 24.5h21v2.2h-21z" fill="#fff" opacity=".22" />
      {/* Barrier post */}
      <rect x="10.6" y="14.4" width="2.6" height="10.1" rx="1" fill="#fff" opacity=".95" />
      {/* Raised boom, in the institutional gold */}
      <rect
        x="12"
        y="9.6"
        width="17.4"
        height="2.9"
        rx="1.45"
        fill="var(--color-gold-500)"
        transform="rotate(-19 12 9.6)"
      />
      {/* Vehicle silhouette approaching */}
      <path
        d="M17.4 21.4h9.2l1.5 2.1v1.1h-12.2v-1.1l1.5-2.1Z"
        fill="#fff"
        opacity=".85"
      />
    </svg>
  )
}

export function Wordmark({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <Logo size={compact ? 26 : 32} />
      <div className="leading-none">
        <div
          className={compact ? 'text-[15px] font-bold tracking-tight' : 'text-[17px] font-bold tracking-tight'}
        >
          SCAE
        </div>
        {!compact && (
          <div className="mt-1 text-[10.5px] font-medium uppercase tracking-[0.13em]"
            style={{ color: 'var(--text-subtle)' }}>
            Universidad de Colima
          </div>
        )}
      </div>
    </div>
  )
}

/** The university motto, used sparingly as an institutional signature. */
export function Motto() {
  return (
    <span className="text-[10.5px] font-medium uppercase tracking-[0.2em]"
      style={{ color: 'var(--text-subtle)' }}>
      Estudia · Lucha · Trabaja
    </span>
  )
}

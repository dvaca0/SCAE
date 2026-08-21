import { useMemo, useState } from 'react'
import type { TrendPoint } from '../lib/types'

/**
 * Stacked access-volume chart.
 *
 * Written by hand rather than pulled from a chart library: the shape we need
 * is narrow (three stacked series over time) and a bespoke SVG keeps the
 * visual language consistent with the rest of the platform.
 */
export function TrendChart({ data, hourly }: { data: TrendPoint[]; hourly: boolean }) {
  const [hover, setHover] = useState<number | null>(null)

  const { bars, max } = useMemo(() => {
    const max = Math.max(1, ...data.map((d) => d.allow + d.deny + d.manual_review))
    return { bars: data, max }
  }, [data])

  if (!data.length) {
    return (
      <div className="grid h-[180px] place-items-center text-[13px]" style={{ color: 'var(--text-subtle)' }}>
        Sin eventos en el periodo seleccionado
      </div>
    )
  }

  const H = 160
  const gap = 3
  const width = 100 / bars.length

  function label(bucket: string): string {
    return hourly ? `${bucket.slice(11, 13)}:00` : bucket.slice(5).replace('-', '/')
  }

  const active = hover !== null ? bars[hover] : null

  return (
    <div className="relative">
      {/* Gridlines */}
      <div className="relative" style={{ height: H }}>
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <div
            key={t}
            className="absolute inset-x-0 border-t"
            style={{ top: `${t * 100}%`, borderColor: 'var(--border)', opacity: t === 1 ? 1 : 0.5 }}
          />
        ))}
        <span
          className="num absolute right-0 top-0.5 rounded px-1 text-[10.5px]"
          style={{ color: 'var(--text-subtle)', background: 'var(--surface-raised)' }}
        >
          {max}
        </span>

        <svg
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          className="absolute inset-0 h-full w-full"
          onMouseLeave={() => setHover(null)}
        >
          {bars.map((d, i) => {
            const total = d.allow + d.deny + d.manual_review
            const x = i * width
            const w = Math.max(0.4, width - gap * (width / 100))
            const hAllow = (d.allow / max) * 100
            const hDeny = (d.deny / max) * 100
            const hRev = (d.manual_review / max) * 100
            const dim = hover !== null && hover !== i
            return (
              <g key={d.bucket} opacity={dim ? 0.35 : 1} onMouseEnter={() => setHover(i)}>
                {/* Invisible hit area so thin bars are still hoverable */}
                <rect x={x} y={0} width={width} height={100} fill="transparent" />
                {total > 0 && (
                  <>
                    <rect x={x} y={100 - hAllow} width={w} height={hAllow} fill="var(--allow)" />
                    <rect x={x} y={100 - hAllow - hDeny} width={w} height={hDeny} fill="var(--deny)" />
                    <rect
                      x={x}
                      y={100 - hAllow - hDeny - hRev}
                      width={w}
                      height={hRev}
                      fill="var(--review)"
                    />
                  </>
                )}
              </g>
            )
          })}
        </svg>
      </div>

      {/* X labels — thinned so they never collide */}
      <div className="mt-2 flex" style={{ color: 'var(--text-subtle)' }}>
        {bars.map((d, i) => (
          <span
            key={d.bucket}
            className="num shrink-0 text-center text-[10px]"
            style={{ width: `${width}%` }}
          >
            {i % Math.ceil(bars.length / 8) === 0 ? label(d.bucket) : ''}
          </span>
        ))}
      </div>

      {active && (
        <div
          className="pointer-events-none absolute -top-1 left-1/2 -translate-x-1/2 rounded-lg border px-3 py-2 text-[12px] shadow-lg"
          style={{
            background: 'var(--surface-raised)',
            borderColor: 'var(--border-strong)',
            boxShadow: 'var(--shadow-pop)',
          }}
        >
          <div className="mb-1 font-semibold">{label(active.bucket)}</div>
          <div className="num space-y-0.5">
            <Row color="var(--allow)" label="Permitidos" value={active.allow} />
            <Row color="var(--deny)" label="Denegados" value={active.deny} />
            <Row color="var(--review)" label="Revisión" value={active.manual_review} />
          </div>
        </div>
      )}
    </div>
  )
}

function Row({ color, label, value }: { color: string; label: string; value: number }) {
  return (
    <div className="flex items-center gap-2 whitespace-nowrap">
      <span className="h-2 w-2 rounded-sm" style={{ background: color }} />
      <span style={{ color: 'var(--text-muted)' }}>{label}</span>
      <span className="ml-auto font-semibold">{value}</span>
    </div>
  )
}

export function ChartLegend() {
  return (
    <div className="flex flex-wrap items-center gap-4 text-[12px]" style={{ color: 'var(--text-muted)' }}>
      {[
        ['var(--allow)', 'Permitidos'],
        ['var(--deny)', 'Denegados'],
        ['var(--review)', 'Revisión manual'],
      ].map(([color, label]) => (
        <span key={label} className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-sm" style={{ background: color }} />
          {label}
        </span>
      ))}
    </div>
  )
}

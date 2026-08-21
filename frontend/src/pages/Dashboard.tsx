import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  Activity, ArrowRight, Car, CheckCircle2, Clock, Gauge,
  KeyRound, ShieldAlert, ShieldX, XCircle,
} from 'lucide-react'
import { get } from '../lib/api'
import type { AccessEvent, DashboardStats, Page } from '../lib/types'
import { count, relative } from '../lib/format'
import {
  Badge, Button, Card, CardHeader, EmptyState, ErrorState, Plate, Select,
  SkeletonRows, Spinner, decisionTone,
} from '../components/ui'
import { ChartLegend, TrendChart } from '../components/TrendChart'
import { DemoNotice, PageHeader } from '../components/AdminLayout'

const RANGES = [
  { value: 24, label: 'Últimas 24 horas' },
  { value: 72, label: 'Últimos 3 días' },
  { value: 168, label: 'Últimos 7 días' },
  { value: 720, label: 'Últimos 30 días' },
]

export function Dashboard() {
  const [hours, setHours] = useState(24)

  const stats = useQuery({
    queryKey: ['dashboard', hours],
    queryFn: () => get<DashboardStats>(`/dashboard?hours=${hours}`),
    refetchInterval: 30_000,
  })

  const recent = useQuery({
    queryKey: ['events', 'recent'],
    queryFn: () => get<Page<AccessEvent>>('/events?page_size=8'),
    refetchInterval: 20_000,
  })

  if (stats.isError) {
    return <ErrorState message={(stats.error as Error).message} onRetry={() => void stats.refetch()} />
  }

  const d = stats.data

  return (
    <>
      <PageHeader
        title="Panel general"
        description="Estado operativo de los accesos vehiculares."
        actions={
          <div className="w-[196px]">
            <Select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
              {RANGES.map((r) => (
                <option key={r.value} value={r.value}>{r.label}</option>
              ))}
            </Select>
          </div>
        }
      />

      {d?.demo_data_present && <DemoNotice />}

      {/* Primary metrics */}
      <div className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric
          label="Eventos registrados" value={d ? count(d.total_events) : null}
          icon={Activity} hint={RANGES.find((r) => r.value === hours)?.label}
        />
        <Metric
          label="Accesos permitidos" value={d ? count(d.allowed) : null}
          icon={CheckCircle2} tone="allow"
          hint={d && d.total_events ? `${Math.round((d.allowed / d.total_events) * 100)}% del total` : undefined}
        />
        <Metric
          label="Accesos denegados" value={d ? count(d.denied) : null}
          icon={XCircle} tone="deny"
          hint={d && d.total_events ? `${Math.round((d.denied / d.total_events) * 100)}% del total` : undefined}
        />
        <Metric
          label="Pendientes de revisión" value={d ? count(d.pending_review) : null}
          icon={Clock} tone={d && d.pending_review > 0 ? 'review' : 'neutral'}
          hint="Requieren decisión de un operador"
          to="/eventos?pendientes=1"
        />
      </div>

      <div className="mb-5 grid gap-5 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        {/* Trend */}
        <Card>
          <CardHeader title="Volumen de accesos" subtitle="Por resultado de la decisión" icon={Gauge} />
          {stats.isLoading ? (
            <Spinner />
          ) : (
            <>
              <TrendChart data={d?.trend ?? []} hourly={hours <= 48} />
              <div className="mt-4 border-t pt-3" style={{ borderColor: 'var(--border)' }}>
                <ChartLegend />
              </div>
            </>
          )}
        </Card>

        {/* Reasons */}
        <Card>
          <CardHeader title="Motivos de decisión" subtitle="Por qué se resolvió cada acceso" icon={KeyRound} />
          {stats.isLoading ? (
            <Spinner />
          ) : !d?.reasons.length ? (
            <EmptyState icon={KeyRound} title="Sin datos en el periodo" />
          ) : (
            <ul className="space-y-3">
              {d.reasons.map((r) => {
                const share = d.total_events ? (r.count / d.total_events) * 100 : 0
                return (
                  <li key={r.key}>
                    <div className="mb-1 flex items-baseline justify-between gap-3 text-[13px]">
                      <span className="truncate">{r.label}</span>
                      <span className="num shrink-0 font-semibold">{count(r.count)}</span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full" style={{ background: 'var(--surface-sunken)' }}>
                      <div
                        className="h-full rounded-full transition-all"
                        style={{
                          width: `${Math.max(2, share)}%`,
                          background: r.key === 'authorized' ? 'var(--allow)'
                            : r.key.includes('watchlist') || r.key.includes('no_authorization') || r.key.includes('suspended')
                            ? 'var(--deny)' : 'var(--review)',
                        }}
                      />
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
        </Card>
      </div>

      {/* Secondary metrics */}
      <div className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MiniStat label="Vehículos registrados" value={d?.registered_vehicles} icon={Car} to="/vehiculos" />
        <MiniStat label="Permisos vigentes" value={d?.active_authorizations} icon={KeyRound} to="/permisos" />
        <MiniStat label="Incidencias abiertas" value={d?.open_incidents} icon={ShieldAlert} to="/incidencias"
          tone={d && d.open_incidents > 0 ? 'review' : undefined} />
        <MiniStat label="Placas en lista de control" value={d?.watchlist_active} icon={ShieldX}
          tone={d && d.watchlist_active > 0 ? 'deny' : undefined} />
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        {/* Recent events */}
        <Card padded={false}>
          <div className="px-5 pt-5">
            <CardHeader
              title="Actividad reciente"
              subtitle="Últimos accesos procesados en todos los carriles"
              icon={Activity}
              action={
                <Link to="/eventos">
                  <Button size="sm" icon={ArrowRight}>Ver todos</Button>
                </Link>
              }
            />
          </div>
          {recent.isLoading ? (
            <SkeletonRows rows={6} cols={4} />
          ) : !recent.data?.items.length ? (
            <EmptyState icon={Activity} title="Sin eventos todavía"
              description="Cuando se procese un acceso aparecerá aquí." />
          ) : (
            <ul className="divide-y" style={{ borderColor: 'var(--border)' }}>
              {recent.data.items.map((e) => (
                <li key={e.id} className="flex items-center gap-3 px-5 py-3">
                  <span
                    className="h-8 w-1 shrink-0 rounded-full"
                    style={{
                      background:
                        e.decision === 'allow' ? 'var(--allow)'
                        : e.decision === 'deny' ? 'var(--deny)' : 'var(--review)',
                    }}
                  />
                  <Plate value={e.plate_display} size="sm" muted />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[13px] font-medium">
                      {e.person_name ?? 'Sin identidad asociada'}
                    </p>
                    <p className="truncate text-[12px]" style={{ color: 'var(--text-subtle)' }}>
                      {e.lane_name ?? 'Sin carril'} · {e.reason_label}
                    </p>
                  </div>
                  <Badge tone={decisionTone(e.decision)}>{e.decision_label}</Badge>
                  <span className="num hidden w-[92px] shrink-0 text-right text-[12px] sm:block"
                    style={{ color: 'var(--text-subtle)' }}>
                    {relative(e.occurred_at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {/* Lanes + performance */}
        <div className="space-y-5">
          <Card>
            <CardHeader title="Carriles con más tránsito" icon={Car} />
            {stats.isLoading ? (
              <Spinner />
            ) : !d?.busiest_lanes.length ? (
              <EmptyState icon={Car} title="Sin tránsito en el periodo" />
            ) : (
              <ul className="space-y-2.5">
                {d.busiest_lanes.map((l, i) => (
                  <li key={l.lane_id} className="flex items-center gap-3">
                    <span
                      className="num grid h-6 w-6 shrink-0 place-items-center rounded-md text-[11px] font-bold"
                      style={{ background: 'var(--surface-sunken)', color: 'var(--text-muted)' }}
                    >
                      {i + 1}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[13px] font-medium">{l.lane}</p>
                      <p className="truncate text-[12px]" style={{ color: 'var(--text-subtle)' }}>
                        {l.access_point}
                      </p>
                    </div>
                    <span className="num text-[13px] font-semibold">{count(l.count)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card>
            <CardHeader title="Desempeño del reconocimiento" icon={Gauge} />
            <dl className="space-y-3">
              <Stat label="Tiempo medio de proceso"
                value={d?.avg_processing_ms ? `${Math.round(d.avg_processing_ms)} ms` : '—'} />
              <Stat label="Vehículos no registrados" value={d ? count(d.unknown_vehicles) : '—'} />
              <Stat label="Turnos activos" value={d ? count(d.active_shifts) : '—'} />
            </dl>
          </Card>
        </div>
      </div>
    </>
  )
}

function Metric({
  label, value, icon: Icon, tone = 'neutral', hint, to,
}: {
  label: string
  value: string | null
  icon: typeof Activity
  tone?: 'neutral' | 'allow' | 'deny' | 'review'
  hint?: string
  to?: string
}) {
  const colors: Record<string, string> = {
    neutral: 'var(--text-muted)',
    allow: 'var(--allow)',
    deny: 'var(--deny)',
    review: 'var(--review)',
  }
  const body = (
    <Card className={to ? 'transition-colors hover:border-[var(--border-strong)]' : undefined}>
      <div className="flex items-start justify-between">
        <p className="text-[12.5px] font-medium" style={{ color: 'var(--text-muted)' }}>{label}</p>
        <Icon size={16} style={{ color: colors[tone] }} />
      </div>
      {value === null ? (
        <div className="mt-2 h-8 w-16 animate-pulse rounded" style={{ background: 'var(--surface-sunken)' }} />
      ) : (
        <p className="num mt-1.5 text-[28px] font-bold leading-none tracking-tight"
          style={{ color: tone === 'neutral' ? 'var(--text)' : colors[tone] }}>
          {value}
        </p>
      )}
      {hint && (
        <p className="mt-1.5 text-[12px]" style={{ color: 'var(--text-subtle)' }}>{hint}</p>
      )}
    </Card>
  )
  return to ? <Link to={to} className="block">{body}</Link> : body
}

function MiniStat({
  label, value, icon: Icon, to, tone,
}: {
  label: string
  value?: number
  icon: typeof Car
  to?: string
  tone?: 'review' | 'deny'
}) {
  const color = tone === 'deny' ? 'var(--deny)' : tone === 'review' ? 'var(--review)' : 'var(--text-muted)'
  const body = (
    <div
      className="surface-card flex items-center gap-3 px-4 py-3 transition-colors hover:border-[var(--border-strong)]"
    >
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg"
        style={{ background: 'var(--surface-sunken)', color }}>
        <Icon size={15} />
      </span>
      <div className="min-w-0">
        <p className="num text-[17px] font-bold leading-none">{value === undefined ? '—' : count(value)}</p>
        <p className="mt-1 truncate text-[12px]" style={{ color: 'var(--text-subtle)' }}>{label}</p>
      </div>
    </div>
  )
  return to ? <Link to={to} className="block">{body}</Link> : body
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-[13px]" style={{ color: 'var(--text-muted)' }}>{label}</dt>
      <dd className="num text-[14px] font-semibold">{value}</dd>
    </div>
  )
}

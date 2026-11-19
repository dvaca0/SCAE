import { useQuery } from '@tanstack/react-query'
import {
  Activity, CheckCircle2, CircleDashed, Database, HeartPulse, TriangleAlert,
} from 'lucide-react'
import { get } from '../lib/api'
import type { Shift, SystemHealth } from '../lib/types'
import { dateTime, relative } from '../lib/format'
import {
  Badge, Card, CardHeader, EmptyState, ErrorState, SkeletonRows, Spinner, type Tone,
} from '../components/ui'
import { PageHeader } from '../components/AdminLayout'

const STATUS: Record<string, { label: string; tone: Tone; Icon: typeof CheckCircle2 }> = {
  operational: { label: 'Operativo', tone: 'allow', Icon: CheckCircle2 },
  simulated: { label: 'Simulado', tone: 'review', Icon: CircleDashed },
  not_implemented: { label: 'No configurado', tone: 'neutral', Icon: CircleDashed },
  degraded: { label: 'Degradado', tone: 'deny', Icon: TriangleAlert },
}

export function SystemStatus() {
  const health = useQuery({
    queryKey: ['system-health'],
    queryFn: () => get<SystemHealth>('/system/health'),
    refetchInterval: 60_000,
  })

  const shifts = useQuery({
    queryKey: ['shifts'],
    queryFn: () => get<Shift[]>('/events/shifts/recent?limit=12'),
  })

  return (
    <>
      <PageHeader
        title="Estado del sistema"
        description="Qué componentes están realmente operativos, cuáles simulados y cuáles pendientes de integración."
      />

      {health.isError ? (
        <ErrorState message={(health.error as Error).message} onRetry={() => void health.refetch()} />
      ) : (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
          <div className="space-y-5">
            <Card>
              <CardHeader title="Componentes" subtitle="Estado real de cada pieza de la plataforma" icon={HeartPulse} />
              {health.isLoading ? (
                <Spinner />
              ) : (
                <ul className="space-y-2.5">
                  {health.data?.components.map((c) => {
                    const s = STATUS[c.status] ?? STATUS.not_implemented
                    return (
                      <li key={c.key} className="flex items-start gap-3 rounded-lg border p-3"
                        style={{ borderColor: 'var(--border)' }}>
                        <s.Icon size={17} className="mt-0.5 shrink-0"
                          style={{
                            color: s.tone === 'allow' ? 'var(--allow)'
                              : s.tone === 'deny' ? 'var(--deny)'
                              : s.tone === 'review' ? 'var(--review)' : 'var(--text-subtle)',
                          }} />
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-[13.5px] font-medium">{c.label}</span>
                            <Badge tone={s.tone}>{s.label}</Badge>
                          </div>
                          <p className="mt-0.5 text-[12.5px]" style={{ color: 'var(--text-muted)' }}>
                            {c.detail}
                          </p>
                        </div>
                      </li>
                    )
                  })}
                </ul>
              )}
            </Card>

            <Card>
              <CardHeader title="Entorno" icon={Database} />
              <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
                <Item label="Versión de la plataforma" value={health.data?.version ?? '—'} />
                <Item label="Entorno" value={health.data?.environment ?? '—'} />
                <Item label="Base de datos" value={health.data?.database === 'ok' ? 'Conectada' : 'Error'} />
                <Item label="Backend de reconocimiento" value={health.data?.recognition_backend ?? '—'} />
              </dl>
              {health.data?.demo_data_present && (
                <p className="mt-4 rounded-lg px-3 py-2.5 text-[12.5px]"
                  style={{ background: 'var(--color-gold-100)', color: 'var(--color-gold-700)' }}>
                  Esta instancia contiene datos de demostración marcados como <strong>Demo</strong>.
                  No representan registros reales de la Universidad de Colima.
                </p>
              )}
            </Card>
          </div>

          <Card>
            <CardHeader title="Turnos de guardia" subtitle="Actividad reciente en las casetas" icon={Activity} />
            {shifts.isLoading ? (
              <SkeletonRows rows={4} cols={2} />
            ) : !shifts.data?.length ? (
              <EmptyState icon={Activity} title="Sin turnos registrados" />
            ) : (
              <ul className="space-y-2.5">
                {shifts.data.map((s) => (
                  <li key={s.id} className="rounded-lg border p-3" style={{ borderColor: 'var(--border)' }}>
                    <div className="flex items-center gap-2">
                      <span className="text-[13px] font-medium">{s.user_name}</span>
                      {s.is_open ? <Badge tone="allow" dot>En turno</Badge> : <Badge tone="neutral">Cerrado</Badge>}
                    </div>
                    <p className="mt-1 text-[12px]" style={{ color: 'var(--text-muted)' }}>
                      {s.lane_name ?? 'Sin carril'} · {dateTime(s.started_at)}
                    </p>
                    <p className="num mt-0.5 text-[11.5px]" style={{ color: 'var(--text-subtle)' }}>
                      {s.duration_minutes != null
                        ? `${Math.floor(s.duration_minutes / 60)} h ${s.duration_minutes % 60} min`
                        : relative(s.started_at)}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      )}
    </>
  )
}

function Item({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: 'var(--text-subtle)' }}>
        {label}
      </dt>
      <dd className="num mt-0.5 text-[13.5px]">{value}</dd>
    </div>
  )
}

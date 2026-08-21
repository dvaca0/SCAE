import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Building2, Camera, Cctv, DoorOpen, MapPin, SignpostBig, Sparkles } from 'lucide-react'
import { get } from '../lib/api'
import type { AccessPoint, Campus, Device, DriverCatalog, Lane } from '../lib/types'
import { LANE_DIRECTION } from '../lib/format'
import {
  Badge, Card, CardHeader, EmptyState, ErrorState, OriginBadge, SkeletonRows, cx,
} from '../components/ui'
import { PageHeader } from '../components/AdminLayout'

export function Infrastructure() {
  const [campusFilter, setCampusFilter] = useState<number | null>(null)

  const campuses = useQuery({ queryKey: ['campuses'], queryFn: () => get<Campus[]>('/catalog/campuses') })
  const points = useQuery({ queryKey: ['access-points'], queryFn: () => get<AccessPoint[]>('/catalog/access-points') })
  const lanes = useQuery({ queryKey: ['lanes'], queryFn: () => get<Lane[]>('/catalog/lanes') })
  const cameras = useQuery({ queryKey: ['cameras'], queryFn: () => get<Device[]>('/catalog/cameras') })
  const controllers = useQuery({ queryKey: ['controllers'], queryFn: () => get<Device[]>('/catalog/controllers') })
  const drivers = useQuery({ queryKey: ['drivers'], queryFn: () => get<DriverCatalog>('/catalog/drivers') })

  const loading = campuses.isLoading || points.isLoading || lanes.isLoading
  const error = campuses.error ?? points.error ?? lanes.error

  const visiblePoints = (points.data ?? []).filter(
    (p) => campusFilter === null || p.campus_id === campusFilter,
  )

  return (
    <>
      <PageHeader
        title="Infraestructura"
        description="Topología física: campus, accesos, carriles y dispositivos instalados."
      />

      {error ? (
        <ErrorState message={(error as Error).message} />
      ) : loading ? (
        <Card padded={false}><SkeletonRows rows={6} cols={4} /></Card>
      ) : (
        <div className="space-y-5">
          {/* Campus selector */}
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => setCampusFilter(null)}
              className={cx(
                'surface-card px-3.5 py-2 text-[13px] font-medium transition-colors',
                campusFilter === null && 'ring-2 ring-uc-500',
              )}
            >
              Todos los campus
            </button>
            {campuses.data?.map((c) => (
              <button
                key={c.id}
                onClick={() => setCampusFilter(c.id)}
                className={cx(
                  'surface-card flex items-center gap-2 px-3.5 py-2 text-[13px] font-medium transition-colors',
                  campusFilter === c.id && 'ring-2 ring-uc-500',
                )}
              >
                <MapPin size={14} style={{ color: 'var(--text-subtle)' }} />
                {c.name}
                <span className="num text-[11.5px]" style={{ color: 'var(--text-subtle)' }}>
                  {c.access_point_count}
                </span>
                <OriginBadge origin={c.origin} />
              </button>
            ))}
          </div>

          {/* Topology */}
          {!visiblePoints.length ? (
            <Card><EmptyState icon={Building2} title="Sin accesos configurados en este campus" /></Card>
          ) : (
            <div className="grid gap-4 lg:grid-cols-2">
              {visiblePoints.map((ap) => {
                const apLanes = (lanes.data ?? []).filter((l) => l.access_point_id === ap.id)
                return (
                  <Card key={ap.id}>
                    <CardHeader
                      title={ap.name}
                      subtitle={`${ap.campus_name} · código ${ap.code}`}
                      icon={DoorOpen}
                      action={
                        <div className="flex items-center gap-1.5">
                          {ap.is_active ? <Badge tone="allow" dot>Activo</Badge> : <Badge tone="neutral">Inactivo</Badge>}
                          <OriginBadge origin={ap.origin} />
                        </div>
                      }
                    />
                    {!apLanes.length ? (
                      <p className="py-3 text-center text-[13px]" style={{ color: 'var(--text-subtle)' }}>
                        Sin carriles configurados
                      </p>
                    ) : (
                      <ul className="space-y-2.5">
                        {apLanes.map((lane) => {
                          const laneCams = (cameras.data ?? []).filter((c) => c.lane_id === lane.id)
                          const laneCtrls = (controllers.data ?? []).filter((c) => c.lane_id === lane.id)
                          return (
                            <li key={lane.id} className="rounded-lg border p-3" style={{ borderColor: 'var(--border)' }}>
                              <div className="flex flex-wrap items-center gap-2">
                                <SignpostBig size={15} style={{ color: 'var(--text-subtle)' }} />
                                <span className="text-[13.5px] font-medium">{lane.name}</span>
                                <Badge tone="info">{LANE_DIRECTION[lane.direction] ?? lane.direction}</Badge>
                                {lane.auto_open_on_allow && <Badge tone="neutral">Apertura automática</Badge>}
                              </div>
                              <div className="mt-2.5 grid gap-2 sm:grid-cols-2">
                                <DeviceList icon={Cctv} label="Cámaras" devices={laneCams} />
                                <DeviceList icon={Camera} label="Controladores" devices={laneCtrls} />
                              </div>
                            </li>
                          )
                        })}
                      </ul>
                    )}
                  </Card>
                )
              })}
            </div>
          )}

          {/* Driver catalog */}
          <Card>
            <CardHeader
              title="Drivers de hardware disponibles"
              subtitle="Interfaces declaradas por la plataforma. Sólo las marcadas como implementadas pueden operar hoy."
              icon={Sparkles}
            />
            <div className="grid gap-5 sm:grid-cols-2">
              <DriverGroup title="Cámaras" items={drivers.data?.cameras ?? []} />
              <DriverGroup title="Barreras" items={drivers.data?.barriers ?? []} />
            </div>
          </Card>
        </div>
      )}
    </>
  )
}

function DeviceList({
  icon: Icon, label, devices,
}: {
  icon: typeof Camera
  label: string
  devices: Device[]
}) {
  return (
    <div className="rounded-md px-2.5 py-2" style={{ background: 'var(--surface-sunken)' }}>
      <p className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider"
        style={{ color: 'var(--text-subtle)' }}>
        <Icon size={12} /> {label}
      </p>
      {!devices.length ? (
        <p className="text-[12px]" style={{ color: 'var(--text-subtle)' }}>Ninguno</p>
      ) : (
        <ul className="space-y-1">
          {devices.map((d) => (
            <li key={d.id} className="flex items-center gap-1.5 text-[12.5px]">
              <span className="h-1.5 w-1.5 shrink-0 rounded-full"
                style={{ background: d.status === 'online' ? 'var(--allow)' : 'var(--text-subtle)' }} />
              <span className="truncate">{d.name}</span>
              <Badge tone={d.driver_implemented ? 'neutral' : 'review'} className="ml-auto shrink-0 !text-[10.5px]">
                {d.driver}
              </Badge>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function DriverGroup({
  title, items,
}: {
  title: string
  items: { kind: string; implemented: boolean; requires_config: string[] }[]
}) {
  return (
    <div>
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider" style={{ color: 'var(--text-subtle)' }}>
        {title}
      </p>
      <ul className="space-y-1.5">
        {items.map((d) => (
          <li key={d.kind} className="flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-[12.5px]"
            style={{ borderColor: 'var(--border)' }}>
            <code className="plate">{d.kind}</code>
            {d.requires_config.length > 0 && (
              <span className="truncate text-[11.5px]" style={{ color: 'var(--text-subtle)' }}>
                requiere {d.requires_config.join(', ')}
              </span>
            )}
            <Badge tone={d.implemented ? 'allow' : 'neutral'} className="ml-auto shrink-0">
              {d.implemented ? 'Implementado' : 'Pendiente'}
            </Badge>
          </li>
        ))}
      </ul>
    </div>
  )
}

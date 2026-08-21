import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { ClipboardList, Filter, Search, X } from 'lucide-react'
import { get, qs } from '../lib/api'
import type { AccessEvent, AccessEventDetail, Page } from '../lib/types'
import { ACTUATION, EVENT_SOURCE, dateTime, percent } from '../lib/format'
import {
  Badge, Button, Card, EmptyState, ErrorState, Input, Modal, OriginBadge,
  Pagination, Plate, Select, SkeletonRows, Table, Td, Th, Tr,
  decisionTone,
} from '../components/ui'
import { PageHeader } from '../components/AdminLayout'

export function Events() {
  const [params, setParams] = useSearchParams()
  const [page, setPage] = useState(1)
  const [decision, setDecision] = useState('')
  const [plate, setPlate] = useState('')
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<number | null>(null)
  const pendingOnly = params.get('pendientes') === '1'

  const query = useQuery({
    queryKey: ['events', 'list', page, decision, plate, pendingOnly],
    queryFn: () =>
      get<Page<AccessEvent>>(
        `/events${qs({ page, page_size: 25, decision, plate, pending_only: pendingOnly || undefined })}`,
      ),
  })

  const detail = useQuery({
    queryKey: ['events', 'detail', selected],
    queryFn: () => get<AccessEventDetail>(`/events/${selected}`),
    enabled: selected !== null,
  })

  function applySearch() {
    setPlate(search.trim())
    setPage(1)
  }

  function clearFilters() {
    setDecision(''); setPlate(''); setSearch(''); setPage(1)
    params.delete('pendientes'); setParams(params)
  }

  const filtered = decision || plate || pendingOnly

  return (
    <>
      <PageHeader
        title="Eventos de acceso"
        description="Registro completo de cada entrada y salida procesada por la plataforma."
        badge={pendingOnly ? <Badge tone="review">Sólo pendientes</Badge> : undefined}
      />

      <Card padded={false}>
        <div className="flex flex-wrap items-center gap-2 border-b p-4" style={{ borderColor: 'var(--border)' }}>
          <div className="relative min-w-[190px] flex-1 sm:max-w-[260px]">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2"
              style={{ color: 'var(--text-subtle)' }} />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value.toUpperCase())}
              onKeyDown={(e) => e.key === 'Enter' && applySearch()}
              placeholder="Buscar placa…"
              className="plate pl-9 uppercase"
            />
          </div>
          <div className="w-[176px]">
            <Select value={decision} onChange={(e) => { setDecision(e.target.value); setPage(1) }}>
              <option value="">Todas las decisiones</option>
              <option value="allow">Permitidos</option>
              <option value="deny">Denegados</option>
              <option value="manual_review">Revisión manual</option>
            </Select>
          </div>
          <Button size="md" icon={Filter} onClick={applySearch}>Filtrar</Button>
          {filtered && (
            <Button size="md" variant="ghost" icon={X} onClick={clearFilters}>Limpiar</Button>
          )}
          <span className="ml-auto text-[12.5px]" style={{ color: 'var(--text-subtle)' }}>
            {query.data ? `${query.data.total.toLocaleString('es-MX')} eventos` : ''}
          </span>
        </div>

        {query.isError ? (
          <ErrorState message={(query.error as Error).message} onRetry={() => void query.refetch()} />
        ) : query.isLoading ? (
          <SkeletonRows rows={8} cols={6} />
        ) : !query.data?.items.length ? (
          <EmptyState icon={ClipboardList} title="Sin eventos que coincidan"
            description="Ajusta los filtros o espera a que se procese un nuevo acceso." />
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th>Fecha y hora</Th>
                  <Th>Placa</Th>
                  <Th>Identidad</Th>
                  <Th>Ubicación</Th>
                  <Th>Resultado</Th>
                  <Th>Motivo</Th>
                  <Th>Origen</Th>
                </tr>
              </thead>
              <tbody>
                {query.data.items.map((e) => (
                  <Tr key={e.id} onClick={() => setSelected(e.id)}>
                    <Td className="num whitespace-nowrap" >{dateTime(e.occurred_at)}</Td>
                    <Td><Plate value={e.plate_display} size="sm" muted /></Td>
                    <Td>
                      <span className="block max-w-[190px] truncate">
                        {e.person_name ?? <span style={{ color: 'var(--text-subtle)' }}>—</span>}
                      </span>
                    </Td>
                    <Td>
                      <span className="block max-w-[170px] truncate" style={{ color: 'var(--text-muted)' }}>
                        {e.lane_name ?? '—'}
                      </span>
                    </Td>
                    <Td><Badge tone={decisionTone(e.decision)}>{e.decision_label}</Badge></Td>
                    <Td>
                      <span className="block max-w-[190px] truncate" style={{ color: 'var(--text-muted)' }}>
                        {e.reason_label}
                      </span>
                    </Td>
                    <Td><OriginBadge origin={e.origin} /></Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
            <Pagination page={page} pageSize={query.data.page_size} total={query.data.total} onPage={setPage} />
          </>
        )}
      </Card>

      <Modal
        open={selected !== null}
        onClose={() => setSelected(null)}
        title={`Evento #${selected ?? ''}`}
        description="Detalle completo de la decisión de acceso"
        width="lg"
        footer={<Button onClick={() => setSelected(null)}>Cerrar</Button>}
      >
        {detail.isLoading ? (
          <SkeletonRows rows={4} cols={2} />
        ) : detail.data ? (
          <EventDetail event={detail.data} />
        ) : null}
      </Modal>
    </>
  )
}

function EventDetail({ event: e }: { event: AccessEventDetail }) {
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <Plate value={e.plate_display} size="lg" />
        <Badge tone={decisionTone(e.decision)}>{e.decision_label}</Badge>
        <Badge tone="neutral">{e.reason_label}</Badge>
        {e.is_manual && <Badge tone="info">Decisión manual</Badge>}
        <OriginBadge origin={e.origin} />
      </div>

      {e.has_evidence && (
        <div>
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider"
            style={{ color: 'var(--text-subtle)' }}>Evidencia capturada</p>
          <img
            src={`/api/v1/events/${e.id}/evidence`}
            alt={`Evidencia del evento ${e.id}`}
            className="max-h-[280px] w-auto rounded-lg border"
            style={{ borderColor: 'var(--border)' }}
          />
        </div>
      )}

      <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
        <Row label="Fecha y hora" value={dateTime(e.occurred_at)} />
        <Row label="Origen del evento" value={EVENT_SOURCE[e.source] ?? e.source} />
        <Row label="Campus" value={e.campus_name ?? '—'} />
        <Row label="Acceso / carril" value={[e.access_point_name, e.lane_name].filter(Boolean).join(' · ') || '—'} />
        <Row label="Identidad" value={e.person_name ?? 'Sin identidad asociada'} />
        <Row label="Vehículo" value={e.vehicle_label ?? 'No registrado'} />
        <Row label="Texto leído por OCR" value={e.plate_read ? <code className="plate">{e.plate_read}</code> : '—'} />
        <Row label="Confianza de lectura" value={percent(e.plate_confidence)} />
        <Row label="Actuación de barrera" value={ACTUATION[e.actuation_status] ?? e.actuation_status} />
        <Row label="Tiempo de proceso" value={e.processing_ms ? `${e.processing_ms} ms` : '—'} />
        <Row label="Resuelto por" value={e.decided_by_name ?? '—'} />
        <Row label="Resuelto el" value={e.resolved_at ? dateTime(e.resolved_at) : 'Pendiente'} />
      </dl>

      {e.notes && (
        <div>
          <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider"
            style={{ color: 'var(--text-subtle)' }}>Notas</p>
          <p className="text-[13px]" style={{ color: 'var(--text-muted)' }}>{e.notes}</p>
        </div>
      )}

      {!!e.plate_candidates?.length && (
        <div>
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider"
            style={{ color: 'var(--text-subtle)' }}>Candidatos de lectura</p>
          <div className="flex flex-wrap gap-1.5">
            {e.plate_candidates.map((c, i) => (
              <span key={`${c.plate}-${i}`} className="plate rounded border px-2 py-1 text-[12px]"
                style={{ borderColor: 'var(--border)' }}>
                {c.display}
                <span className="num ml-1.5 opacity-60">{Math.round(c.score * 100)}%</span>
                {c.repaired && <span className="ml-1 text-[10px] opacity-60">corregida</span>}
              </span>
            ))}
          </div>
        </div>
      )}

      {!!e.detections.length && (
        <div>
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider"
            style={{ color: 'var(--text-subtle)' }}>Detecciones</p>
          <ul className="space-y-1 text-[12.5px]" style={{ color: 'var(--text-muted)' }}>
            {e.detections.map((d) => (
              <li key={d.id}>
                {d.detector} · {d.object_class ?? 'sin objeto'}
                {d.object_confidence != null && ` (${percent(d.object_confidence)})`}
                {d.ocr_engine && ` · OCR ${d.ocr_engine}`}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: 'var(--text-subtle)' }}>
        {label}
      </dt>
      <dd className="mt-0.5 text-[13.5px]">{value}</dd>
    </div>
  )
}

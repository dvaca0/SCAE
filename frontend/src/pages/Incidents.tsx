import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { MailWarning, ShieldAlert } from 'lucide-react'
import { get, patch, qs } from '../lib/api'
import type { Incident, Page } from '../lib/types'
import { INCIDENT_STATUS, INCIDENT_TYPES, SEVERITY, dateTime } from '../lib/format'
import {
  Badge, Button, Card, EmptyState, ErrorState, Modal, OriginBadge, Pagination,
  Plate, Select, SkeletonRows, Table, Td, Th, Textarea, Tr, type Tone,
} from '../components/ui'
import { PageHeader } from '../components/AdminLayout'
import { can, useAuth } from '../lib/auth'

const severityTone: Record<string, Tone> = {
  low: 'neutral', medium: 'review', high: 'deny', critical: 'deny',
}
const statusTone: Record<string, Tone> = {
  open: 'review', in_review: 'info', resolved: 'allow', dismissed: 'neutral',
}

export function Incidents() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState('')
  const [selected, setSelected] = useState<Incident | null>(null)
  const [notes, setNotes] = useState('')

  const query = useQuery({
    queryKey: ['incidents', page, statusFilter],
    queryFn: () => get<Page<Incident>>(`/incidents${qs({ page, page_size: 25, status: statusFilter })}`),
  })

  const update = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) =>
      patch<Incident>(`/incidents/${id}`, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['incidents'] })
      setSelected(null); setNotes('')
    },
  })

  const editable = can.manageRegistry(user?.role)

  return (
    <>
      <PageHeader
        title="Incidencias"
        description="Infracciones, intentos no autorizados y coincidencias con la lista de control."
      />

      <Card padded={false}>
        <div className="flex flex-wrap items-center gap-2 border-b p-4" style={{ borderColor: 'var(--border)' }}>
          <div className="w-[180px]">
            <Select value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); setPage(1) }}>
              <option value="">Todos los estados</option>
              {Object.entries(INCIDENT_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
          </div>
          <span className="ml-auto text-[12.5px]" style={{ color: 'var(--text-subtle)' }}>
            {query.data ? `${query.data.total} incidencias` : ''}
          </span>
        </div>

        {query.isError ? (
          <ErrorState message={(query.error as Error).message} onRetry={() => void query.refetch()} />
        ) : query.isLoading ? (
          <SkeletonRows rows={6} cols={6} />
        ) : !query.data?.items.length ? (
          <EmptyState icon={ShieldAlert} title="Sin incidencias registradas"
            description="Las incidencias se generan automáticamente al denegar un acceso, o las levanta un operador." />
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th>Fecha</Th><Th>Asunto</Th><Th>Placa</Th>
                  <Th>Tipo</Th><Th>Severidad</Th><Th>Estado</Th><Th>Notificación</Th>
                </tr>
              </thead>
              <tbody>
                {query.data.items.map((i) => (
                  <Tr key={i.id} onClick={() => { setSelected(i); setNotes(i.resolution_notes ?? '') }}>
                    <Td className="num whitespace-nowrap">{dateTime(i.created_at)}</Td>
                    <Td>
                      <span className="block max-w-[260px] truncate font-medium">{i.title}</span>
                      {i.person_name && (
                        <span className="text-[12px]" style={{ color: 'var(--text-subtle)' }}>{i.person_name}</span>
                      )}
                    </Td>
                    <Td><Plate value={i.plate_display} size="sm" muted /></Td>
                    <Td>
                      <span className="text-[12.5px]" style={{ color: 'var(--text-muted)' }}>
                        {INCIDENT_TYPES[i.incident_type] ?? i.incident_type}
                      </span>
                    </Td>
                    <Td><Badge tone={severityTone[i.severity] ?? 'neutral'}>{SEVERITY[i.severity] ?? i.severity}</Badge></Td>
                    <Td>
                      <div className="flex items-center gap-1.5">
                        <Badge tone={statusTone[i.status] ?? 'neutral'}>{INCIDENT_STATUS[i.status] ?? i.status}</Badge>
                        <OriginBadge origin={i.origin} />
                      </div>
                    </Td>
                    <Td>
                      {i.notified_at ? (
                        <Badge tone="allow">Enviada</Badge>
                      ) : i.notification_error ? (
                        <span className="flex items-center gap-1 text-[12px]" style={{ color: 'var(--review)' }}
                          title={i.notification_error}>
                          <MailWarning size={13} /> No enviada
                        </span>
                      ) : (
                        <span style={{ color: 'var(--text-subtle)' }}>—</span>
                      )}
                    </Td>
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
        title={selected?.title ?? ''}
        description={selected ? `Incidencia #${selected.id} · ${dateTime(selected.created_at)}` : undefined}
        footer={
          <>
            <Button onClick={() => setSelected(null)}>Cerrar</Button>
            {editable && selected && selected.status !== 'resolved' && (
              <>
                <Button
                  loading={update.isPending}
                  onClick={() => update.mutate({ id: selected.id, body: { status: 'dismissed', resolution_notes: notes } })}
                >
                  Descartar
                </Button>
                <Button
                  variant="primary"
                  loading={update.isPending}
                  onClick={() => update.mutate({ id: selected.id, body: { status: 'resolved', resolution_notes: notes } })}
                >
                  Marcar resuelta
                </Button>
              </>
            )}
          </>
        }
      >
        {selected && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={statusTone[selected.status]}>{INCIDENT_STATUS[selected.status]}</Badge>
              <Badge tone={severityTone[selected.severity]}>{SEVERITY[selected.severity]}</Badge>
              <Badge tone="neutral">{INCIDENT_TYPES[selected.incident_type]}</Badge>
              {selected.plate_display && <Plate value={selected.plate_display} size="sm" muted />}
            </div>

            {selected.description && (
              <p className="text-[13.5px]" style={{ color: 'var(--text-muted)' }}>{selected.description}</p>
            )}

            <dl className="grid gap-x-6 gap-y-2.5 text-[13px] sm:grid-cols-2">
              <Pair label="Persona" value={selected.person_name ?? '—'} />
              <Pair label="Carril" value={selected.lane_name ?? '—'} />
              <Pair label="Reportada por" value={selected.reported_by_name ?? '—'} />
              <Pair label="Resuelta por" value={selected.resolved_by_name ?? '—'} />
            </dl>

            {selected.notification_error && (
              <div className="flex items-start gap-2 rounded-lg px-3 py-2.5 text-[12.5px]"
                style={{ background: 'var(--review-bg)', color: 'var(--review)' }}>
                <MailWarning size={15} className="mt-px shrink-0" />
                <span>La notificación no se envió: {selected.notification_error}</span>
              </div>
            )}

            {editable && selected.status !== 'resolved' && (
              <div>
                <p className="mb-1.5 text-[13px] font-medium">Notas de resolución</p>
                <Textarea value={notes} onChange={(e) => setNotes(e.target.value)}
                  placeholder="Describe cómo se atendió la incidencia…" />
              </div>
            )}
            {selected.resolution_notes && selected.status === 'resolved' && (
              <div>
                <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider"
                  style={{ color: 'var(--text-subtle)' }}>Resolución</p>
                <p className="text-[13px]" style={{ color: 'var(--text-muted)' }}>{selected.resolution_notes}</p>
              </div>
            )}
          </div>
        )}
      </Modal>
    </>
  )
}

function Pair({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: 'var(--text-subtle)' }}>
        {label}
      </dt>
      <dd className="mt-0.5">{value}</dd>
    </div>
  )
}

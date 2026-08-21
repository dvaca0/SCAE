import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Plus, ShieldX, Trash2 } from 'lucide-react'
import { ApiError, del, get, patch, post, qs } from '../lib/api'
import type { AccessPoint, Authorization, Campus, Page, Vehicle, WatchlistEntry } from '../lib/types'
import { AUTH_STATUS, SEVERITY, dateOnly, dateTime } from '../lib/format'
import {
  Badge, Button, Card, CardHeader, EmptyState, ErrorState, Field, Input, Modal,
  OriginBadge, Pagination, Plate, Select, SkeletonRows, Table, Td, Th, Textarea, Tr,
} from '../components/ui'
import { PageHeader } from '../components/AdminLayout'
import { can, useAuth } from '../lib/auth'

export function Authorizations() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState('')
  const [creating, setCreating] = useState(false)
  const [addingWatch, setAddingWatch] = useState(false)

  const query = useQuery({
    queryKey: ['authorizations', page, statusFilter],
    queryFn: () => get<Page<Authorization>>(`/authorizations${qs({ page, page_size: 25, status: statusFilter })}`),
  })

  const watchlist = useQuery({
    queryKey: ['watchlist'],
    queryFn: () => get<WatchlistEntry[]>('/watchlist'),
  })

  const vehicles = useQuery({
    queryKey: ['vehicles', 'all'],
    queryFn: () => get<Page<Vehicle>>('/vehicles?page_size=200'),
    enabled: creating,
  })
  const campuses = useQuery({
    queryKey: ['campuses'], queryFn: () => get<Campus[]>('/catalog/campuses'), enabled: creating,
  })
  const points = useQuery({
    queryKey: ['access-points'], queryFn: () => get<AccessPoint[]>('/catalog/access-points'), enabled: creating,
  })

  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) => post<Authorization>('/authorizations', body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['authorizations'] })
      setCreating(false)
    },
  })

  const changeStatus = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) =>
      patch(`/authorizations/${id}/status`, { status }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['authorizations'] }),
  })

  const addWatch = useMutation({
    mutationFn: (body: Record<string, unknown>) => post<WatchlistEntry>('/watchlist', body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['watchlist'] })
      setAddingWatch(false)
    },
  })

  const removeWatch = useMutation({
    mutationFn: (id: number) => del(`/watchlist/${id}`),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['watchlist'] }),
  })

  const editable = can.manageRegistry(user?.role)

  return (
    <>
      <PageHeader
        title="Permisos de acceso"
        description="Autorizaciones vigentes y lista de control de seguridad."
        actions={editable && (
          <Button variant="primary" icon={Plus} onClick={() => { create.reset(); setCreating(true) }}>
            Emitir permiso
          </Button>
        )}
      />

      <div className="grid gap-5 2xl:grid-cols-[minmax(0,1.75fr)_minmax(0,1fr)]">
        <Card padded={false}>
          <div className="flex flex-wrap items-center gap-2 border-b p-4" style={{ borderColor: 'var(--border)' }}>
            <div className="w-[180px]">
              <Select value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); setPage(1) }}>
                <option value="">Todos los estados</option>
                {Object.entries(AUTH_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select>
            </div>
            <span className="ml-auto text-[12.5px]" style={{ color: 'var(--text-subtle)' }}>
              {query.data ? `${query.data.total} permisos` : ''}
            </span>
          </div>

          {query.isError ? (
            <ErrorState message={(query.error as Error).message} onRetry={() => void query.refetch()} />
          ) : query.isLoading ? (
            <SkeletonRows rows={7} cols={5} />
          ) : !query.data?.items.length ? (
            <EmptyState icon={KeyRound} title="Sin permisos emitidos"
              description="Emite un permiso para autorizar el acceso de un vehículo." />
          ) : (
            <>
              <Table>
                <thead>
                  <tr>
                    <Th>Placa</Th><Th>Titular</Th><Th>Alcance</Th>
                    <Th>Vigencia</Th><Th>Estado</Th>{editable && <Th />}
                  </tr>
                </thead>
                <tbody>
                  {query.data.items.map((a) => (
                    <Tr key={a.id}>
                      <Td><Plate value={a.plate_display} size="sm" muted /></Td>
                      <Td>
                        <span className="block max-w-[180px] truncate">
                          {a.person_name ?? <span style={{ color: 'var(--text-subtle)' }}>—</span>}
                        </span>
                        {a.permit_code && (
                          <span className="plate text-[11px]" style={{ color: 'var(--text-subtle)' }}>
                            {a.permit_code}
                          </span>
                        )}
                      </Td>
                      <Td>
                        <span className="text-[12.5px]" style={{ color: 'var(--text-muted)' }}>
                          {a.access_point_name ?? a.campus_name ?? a.scope_label}
                        </span>
                      </Td>
                      <Td className="num whitespace-nowrap text-[12.5px]">
                        {dateOnly(a.valid_from)}
                        <span style={{ color: 'var(--text-subtle)' }}>
                          {' → '}{a.valid_until ? dateOnly(a.valid_until) : 'sin fin'}
                        </span>
                      </Td>
                      <Td>
                        <div className="flex items-center gap-1.5">
                          <Badge tone={a.is_currently_valid ? 'allow' : a.status === 'suspended' ? 'deny' : 'neutral'}>
                            {AUTH_STATUS[a.status] ?? a.status}
                          </Badge>
                          <OriginBadge origin={a.origin} />
                        </div>
                      </Td>
                      {editable && (
                        <Td>
                          <div className="w-[132px]">
                            <Select
                              value={a.status}
                              onChange={(e) => changeStatus.mutate({ id: a.id, status: e.target.value })}
                              className="!py-1 !text-[12px]"
                              aria-label="Cambiar estado"
                            >
                              {Object.entries(AUTH_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                            </Select>
                          </div>
                        </Td>
                      )}
                    </Tr>
                  ))}
                </tbody>
              </Table>
              <Pagination page={page} pageSize={query.data.page_size} total={query.data.total} onPage={setPage} />
            </>
          )}
        </Card>

        {/* Watchlist */}
        <Card>
          <CardHeader
            title="Lista de control"
            subtitle="Placas que se deniegan por encima de cualquier permiso"
            icon={ShieldX}
            action={editable && (
              <Button size="sm" icon={Plus} onClick={() => { addWatch.reset(); setAddingWatch(true) }}>
                Añadir
              </Button>
            )}
          />
          {watchlist.isLoading ? (
            <SkeletonRows rows={3} cols={2} />
          ) : !watchlist.data?.length ? (
            <EmptyState icon={ShieldX} title="Lista vacía"
              description="No hay placas marcadas por seguridad." />
          ) : (
            <ul className="space-y-2.5">
              {watchlist.data.map((w) => (
                <li key={w.id} className="rounded-lg border p-3" style={{ borderColor: 'var(--border)' }}>
                  <div className="flex items-center gap-2">
                    <Plate value={w.plate_display} size="sm" />
                    <Badge tone="deny">{SEVERITY[w.severity] ?? w.severity}</Badge>
                    <OriginBadge origin={w.origin} />
                    {editable && (
                      <Button size="sm" variant="ghost" icon={Trash2} className="ml-auto !px-2"
                        aria-label="Desactivar"
                        onClick={() => removeWatch.mutate(w.id)} />
                    )}
                  </div>
                  <p className="mt-1.5 text-[12.5px]" style={{ color: 'var(--text-muted)' }}>{w.reason}</p>
                  <p className="num mt-1 text-[11px]" style={{ color: 'var(--text-subtle)' }}>
                    {dateTime(w.created_at)}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <AuthorizationModal
        open={creating}
        onClose={() => setCreating(false)}
        vehicles={vehicles.data?.items ?? []}
        campuses={campuses.data ?? []}
        points={points.data ?? []}
        onSubmit={(b) => create.mutate(b)}
        pending={create.isPending}
        error={create.error instanceof ApiError ? create.error.message : null}
      />

      <WatchlistModal
        open={addingWatch}
        onClose={() => setAddingWatch(false)}
        onSubmit={(b) => addWatch.mutate(b)}
        pending={addWatch.isPending}
        error={addWatch.error instanceof ApiError ? addWatch.error.message : null}
      />
    </>
  )
}

function AuthorizationModal({
  open, onClose, vehicles, campuses, points, onSubmit, pending, error,
}: {
  open: boolean
  onClose: () => void
  vehicles: Vehicle[]
  campuses: Campus[]
  points: AccessPoint[]
  onSubmit: (b: Record<string, unknown>) => void
  pending: boolean
  error: string | null
}) {
  const today = new Date().toISOString().slice(0, 10)
  const [form, setForm] = useState({
    vehicle_id: '', scope: 'campus', campus_id: '', access_point_id: '',
    valid_from: today, valid_until: '', permit_code: '', reason: '',
  })
  const set = (k: string) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.target.value }))

  return (
    <Modal
      open={open} onClose={onClose}
      title="Emitir permiso de acceso"
      description="Define a qué vehículo, dónde y durante cuánto tiempo aplica."
      footer={
        <>
          <Button onClick={onClose}>Cancelar</Button>
          <Button variant="primary" loading={pending} disabled={!form.vehicle_id}
            onClick={() => onSubmit({
              vehicle_id: Number(form.vehicle_id),
              campus_id: form.scope === 'campus' && form.campus_id ? Number(form.campus_id) : null,
              access_point_id: form.scope === 'point' && form.access_point_id ? Number(form.access_point_id) : null,
              valid_from: new Date(form.valid_from).toISOString(),
              valid_until: form.valid_until ? new Date(form.valid_until + 'T23:59:59').toISOString() : null,
              permit_code: form.permit_code || null,
              reason: form.reason || null,
              status: 'active',
            })}>
            Emitir
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Vehículo" required>
          <Select value={form.vehicle_id} onChange={set('vehicle_id')} autoFocus>
            <option value="">Selecciona un vehículo…</option>
            {vehicles.map((v) => (
              <option key={v.id} value={v.id}>
                {v.plate_display}{v.owner_name ? ` · ${v.owner_name}` : ''}
              </option>
            ))}
          </Select>
        </Field>

        <Field label="Alcance" hint="Determina en qué accesos es válido el permiso.">
          <Select value={form.scope} onChange={set('scope')}>
            <option value="institution">Toda la institución</option>
            <option value="campus">Un campus completo</option>
            <option value="point">Un acceso específico</option>
          </Select>
        </Field>

        {form.scope === 'campus' && (
          <Field label="Campus" required>
            <Select value={form.campus_id} onChange={set('campus_id')}>
              <option value="">Selecciona…</option>
              {campuses.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </Select>
          </Field>
        )}
        {form.scope === 'point' && (
          <Field label="Acceso" required>
            <Select value={form.access_point_id} onChange={set('access_point_id')}>
              <option value="">Selecciona…</option>
              {points.map((p) => (
                <option key={p.id} value={p.id}>{p.name} · {p.campus_name}</option>
              ))}
            </Select>
          </Field>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Vigente desde" required>
            <Input type="date" value={form.valid_from} onChange={set('valid_from')} />
          </Field>
          <Field label="Vigente hasta" hint="Vacío = sin fecha de término.">
            <Input type="date" value={form.valid_until} onChange={set('valid_until')} />
          </Field>
        </div>

        <Field label="Folio del permiso">
          <Input value={form.permit_code} onChange={set('permit_code')} className="plate" placeholder="UC-2026-0001" />
        </Field>
        <Field label="Motivo / observaciones">
          <Textarea value={form.reason} onChange={set('reason')} />
        </Field>

        {error && (
          <p className="rounded-lg px-3 py-2 text-[13px]"
            style={{ background: 'var(--deny-bg)', color: 'var(--deny)' }}>{error}</p>
        )}
      </div>
    </Modal>
  )
}

function WatchlistModal({
  open, onClose, onSubmit, pending, error,
}: {
  open: boolean
  onClose: () => void
  onSubmit: (b: Record<string, unknown>) => void
  pending: boolean
  error: string | null
}) {
  const [form, setForm] = useState({ plate: '', severity: 'high', reason: '' })
  const set = (k: string) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.target.value }))

  return (
    <Modal
      open={open} onClose={onClose}
      title="Añadir a lista de control"
      description="Una placa en esta lista se deniega aunque tenga permiso vigente."
      footer={
        <>
          <Button onClick={onClose}>Cancelar</Button>
          <Button variant="danger" loading={pending}
            disabled={form.plate.trim().length < 4 || form.reason.trim().length < 3}
            onClick={() => onSubmit(form)}>
            Añadir
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Placa" required>
          <Input value={form.plate} onChange={set('plate')} className="plate uppercase" autoFocus />
        </Field>
        <Field label="Severidad" required>
          <Select value={form.severity} onChange={set('severity')}>
            {Object.entries(SEVERITY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        </Field>
        <Field label="Motivo" required hint="Queda registrado en la auditoría.">
          <Textarea value={form.reason} onChange={set('reason')} />
        </Field>
        {error && (
          <p className="rounded-lg px-3 py-2 text-[13px]"
            style={{ background: 'var(--deny-bg)', color: 'var(--deny)' }}>{error}</p>
        )}
      </div>
    </Modal>
  )
}

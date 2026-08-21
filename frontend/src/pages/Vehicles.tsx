import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Car, Plus, Search, ShieldX } from 'lucide-react'
import { get, post, qs } from '../lib/api'
import type { Page, Person, Vehicle } from '../lib/types'
import {
  Badge, Button, Card, EmptyState, ErrorState, Field, Input, Modal, OriginBadge,
  Pagination, Plate, Select, SkeletonRows, Table, Td, Th, Tr,
} from '../components/ui'
import { PageHeader } from '../components/AdminLayout'
import { can, useAuth } from '../lib/auth'
import { ApiError } from '../lib/api'

export function Vehicles() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [applied, setApplied] = useState('')
  const [creating, setCreating] = useState(false)

  const query = useQuery({
    queryKey: ['vehicles', page, applied],
    queryFn: () => get<Page<Vehicle>>(`/vehicles${qs({ page, page_size: 25, q: applied })}`),
  })

  const people = useQuery({
    queryKey: ['people', 'all'],
    queryFn: () => get<Page<Person>>('/people?page_size=200'),
    enabled: creating,
  })

  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) => post<Vehicle>('/vehicles', body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['vehicles'] })
      setCreating(false)
    },
  })

  return (
    <>
      <PageHeader
        title="Vehículos"
        description="Padrón de vehículos reconocidos por la plataforma."
        actions={
          can.manageRegistry(user?.role) && (
            <Button variant="primary" icon={Plus} onClick={() => { create.reset(); setCreating(true) }}>
              Registrar vehículo
            </Button>
          )
        }
      />

      <Card padded={false}>
        <div className="flex flex-wrap items-center gap-2 border-b p-4" style={{ borderColor: 'var(--border)' }}>
          <div className="relative min-w-[200px] flex-1 sm:max-w-[300px]">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2"
              style={{ color: 'var(--text-subtle)' }} />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { setApplied(search.trim()); setPage(1) } }}
              placeholder="Placa, marca o modelo…"
              className="pl-9"
            />
          </div>
          <Button onClick={() => { setApplied(search.trim()); setPage(1) }}>Buscar</Button>
          <span className="ml-auto text-[12.5px]" style={{ color: 'var(--text-subtle)' }}>
            {query.data ? `${query.data.total.toLocaleString('es-MX')} vehículos` : ''}
          </span>
        </div>

        {query.isError ? (
          <ErrorState message={(query.error as Error).message} onRetry={() => void query.refetch()} />
        ) : query.isLoading ? (
          <SkeletonRows rows={8} cols={5} />
        ) : !query.data?.items.length ? (
          <EmptyState icon={Car} title="Sin vehículos registrados"
            description="Registra un vehículo para poder asignarle permisos de acceso." />
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th>Placa</Th>
                  <Th>Vehículo</Th>
                  <Th>Propietario</Th>
                  <Th>Permisos vigentes</Th>
                  <Th>Estado</Th>
                </tr>
              </thead>
              <tbody>
                {query.data.items.map((v) => (
                  <Tr key={v.id}>
                    <Td>
                      <div className="flex items-center gap-2">
                        <Plate value={v.plate_display} size="sm" />
                        {v.is_watchlisted && (
                          <ShieldX size={14} style={{ color: 'var(--deny)' }}
                            aria-label="En lista de control" />
                        )}
                      </div>
                    </Td>
                    <Td>
                      <span className="block max-w-[210px] truncate">
                        {[v.brand, v.model].filter(Boolean).join(' ') || '—'}
                      </span>
                      <span className="text-[12px]" style={{ color: 'var(--text-subtle)' }}>
                        {[v.color, v.year].filter(Boolean).join(' · ')}
                      </span>
                    </Td>
                    <Td>
                      <span className="block max-w-[200px] truncate">
                        {v.owner_name ?? <span style={{ color: 'var(--text-subtle)' }}>Sin asignar</span>}
                      </span>
                    </Td>
                    <Td>
                      {v.active_authorizations > 0 ? (
                        <Badge tone="allow">{v.active_authorizations} vigente{v.active_authorizations > 1 ? 's' : ''}</Badge>
                      ) : (
                        <Badge tone="neutral">Ninguno</Badge>
                      )}
                    </Td>
                    <Td>
                      <div className="flex items-center gap-1.5">
                        {v.is_active ? <Badge tone="allow" dot>Activo</Badge> : <Badge tone="neutral">Inactivo</Badge>}
                        <OriginBadge origin={v.origin} />
                      </div>
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
            <Pagination page={page} pageSize={query.data.page_size} total={query.data.total} onPage={setPage} />
          </>
        )}
      </Card>

      <VehicleModal
        open={creating}
        onClose={() => setCreating(false)}
        people={people.data?.items ?? []}
        onSubmit={(body) => create.mutate(body)}
        pending={create.isPending}
        error={create.error instanceof ApiError ? create.error.message : null}
      />
    </>
  )
}

function VehicleModal({
  open, onClose, people, onSubmit, pending, error,
}: {
  open: boolean
  onClose: () => void
  people: Person[]
  onSubmit: (body: Record<string, unknown>) => void
  pending: boolean
  error: string | null
}) {
  const [form, setForm] = useState({
    plate: '', brand: '', model: '', color: '', year: '', owner_id: '', state_code: 'COL',
  })
  const set = (k: string) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.target.value }))

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Registrar vehículo"
      description="La placa se normaliza automáticamente al formato institucional."
      footer={
        <>
          <Button onClick={onClose}>Cancelar</Button>
          <Button
            variant="primary"
            loading={pending}
            disabled={form.plate.trim().length < 4}
            onClick={() =>
              onSubmit({
                plate: form.plate.trim(),
                brand: form.brand || null,
                model: form.model || null,
                color: form.color || null,
                year: form.year ? Number(form.year) : null,
                owner_id: form.owner_id ? Number(form.owner_id) : null,
                state_code: form.state_code || null,
              })
            }
          >
            Registrar
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Placa" required hint="Ejemplo: GPT-45-67, ABC-123-D, 12-AB-34">
          <Input value={form.plate} onChange={set('plate')} className="plate uppercase"
            placeholder="GPT-45-67" autoFocus />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Marca"><Input value={form.brand} onChange={set('brand')} placeholder="Nissan" /></Field>
          <Field label="Modelo"><Input value={form.model} onChange={set('model')} placeholder="Versa" /></Field>
          <Field label="Color"><Input value={form.color} onChange={set('color')} placeholder="Blanco" /></Field>
          <Field label="Año"><Input value={form.year} onChange={set('year')} type="number" placeholder="2021" /></Field>
        </div>
        <Field label="Propietario" hint="Puede asignarse después.">
          <Select value={form.owner_id} onChange={set('owner_id')}>
            <option value="">Sin asignar</option>
            {people.map((p) => (
              <option key={p.id} value={p.id}>
                {p.full_name}{p.institutional_id ? ` · ${p.institutional_id}` : ''}
              </option>
            ))}
          </Select>
        </Field>
        {error && (
          <p className="rounded-lg px-3 py-2 text-[13px]"
            style={{ background: 'var(--deny-bg)', color: 'var(--deny)' }}>{error}</p>
        )}
      </div>
    </Modal>
  )
}

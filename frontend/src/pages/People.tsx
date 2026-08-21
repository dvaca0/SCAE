import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Search, Users as UsersIcon } from 'lucide-react'
import { ApiError, get, post, qs } from '../lib/api'
import type { Page, Person } from '../lib/types'
import { PERSON_TYPES } from '../lib/format'
import {
  Badge, Button, Card, EmptyState, ErrorState, Field, Input, Modal, OriginBadge,
  Pagination, Select, SkeletonRows, Table, Td, Th, Tr,
} from '../components/ui'
import { PageHeader } from '../components/AdminLayout'
import { can, useAuth } from '../lib/auth'

export function People() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [applied, setApplied] = useState('')
  const [type, setType] = useState('')
  const [creating, setCreating] = useState(false)

  const query = useQuery({
    queryKey: ['people', page, applied, type],
    queryFn: () => get<Page<Person>>(`/people${qs({ page, page_size: 25, q: applied, person_type: type })}`),
  })

  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) => post<Person>('/people', body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['people'] })
      setCreating(false)
    },
  })

  return (
    <>
      <PageHeader
        title="Personas"
        description="Padrón de personas asociadas a vehículos y permisos."
        actions={
          can.manageRegistry(user?.role) && (
            <Button variant="primary" icon={Plus} onClick={() => { create.reset(); setCreating(true) }}>
              Registrar persona
            </Button>
          )
        }
      />

      <Card padded={false}>
        <div className="flex flex-wrap items-center gap-2 border-b p-4" style={{ borderColor: 'var(--border)' }}>
          <div className="relative min-w-[200px] flex-1 sm:max-w-[300px]">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2"
              style={{ color: 'var(--text-subtle)' }} />
            <Input value={search} onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { setApplied(search.trim()); setPage(1) } }}
              placeholder="Nombre, matrícula o correo…" className="pl-9" />
          </div>
          <div className="w-[176px]">
            <Select value={type} onChange={(e) => { setType(e.target.value); setPage(1) }}>
              <option value="">Todos los tipos</option>
              {Object.entries(PERSON_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
          </div>
          <Button onClick={() => { setApplied(search.trim()); setPage(1) }}>Buscar</Button>
          <span className="ml-auto text-[12.5px]" style={{ color: 'var(--text-subtle)' }}>
            {query.data ? `${query.data.total.toLocaleString('es-MX')} personas` : ''}
          </span>
        </div>

        {query.isError ? (
          <ErrorState message={(query.error as Error).message} onRetry={() => void query.refetch()} />
        ) : query.isLoading ? (
          <SkeletonRows rows={8} cols={5} />
        ) : !query.data?.items.length ? (
          <EmptyState icon={UsersIcon} title="Sin personas registradas" />
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th>Nombre</Th><Th>Identificador</Th><Th>Tipo</Th>
                  <Th>Adscripción</Th><Th>Vehículos</Th><Th>Estado</Th>
                </tr>
              </thead>
              <tbody>
                {query.data.items.map((p) => (
                  <Tr key={p.id}>
                    <Td>
                      <span className="font-medium">{p.full_name}</span>
                      {p.email && (
                        <span className="block max-w-[220px] truncate text-[12px]"
                          style={{ color: 'var(--text-subtle)' }}>{p.email}</span>
                      )}
                    </Td>
                    <Td><code className="plate text-[12px]">{p.institutional_id ?? '—'}</code></Td>
                    <Td><Badge tone="info">{PERSON_TYPES[p.person_type] ?? p.person_type}</Badge></Td>
                    <Td>
                      <span className="block max-w-[240px] truncate" style={{ color: 'var(--text-muted)' }}>
                        {p.faculty ?? '—'}
                      </span>
                    </Td>
                    <Td className="num">{p.vehicle_count}</Td>
                    <Td>
                      <div className="flex items-center gap-1.5">
                        {p.is_active ? <Badge tone="allow" dot>Activa</Badge> : <Badge tone="neutral">Inactiva</Badge>}
                        <OriginBadge origin={p.origin} />
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

      <PersonModal
        open={creating}
        onClose={() => setCreating(false)}
        onSubmit={(body) => create.mutate(body)}
        pending={create.isPending}
        error={create.error instanceof ApiError ? create.error.message : null}
      />
    </>
  )
}

function PersonModal({
  open, onClose, onSubmit, pending, error,
}: {
  open: boolean
  onClose: () => void
  onSubmit: (body: Record<string, unknown>) => void
  pending: boolean
  error: string | null
}) {
  const [form, setForm] = useState({
    first_name: '', last_name: '', institutional_id: '', person_type: 'student',
    email: '', phone: '', faculty: '',
  })
  const set = (k: string) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.target.value }))

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Registrar persona"
      footer={
        <>
          <Button onClick={onClose}>Cancelar</Button>
          <Button variant="primary" loading={pending}
            disabled={!form.first_name.trim() || !form.last_name.trim()}
            onClick={() => onSubmit({
              ...form,
              institutional_id: form.institutional_id || null,
              email: form.email || null,
              phone: form.phone || null,
              faculty: form.faculty || null,
            })}>
            Registrar
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Nombre(s)" required>
            <Input value={form.first_name} onChange={set('first_name')} autoFocus />
          </Field>
          <Field label="Apellidos" required>
            <Input value={form.last_name} onChange={set('last_name')} />
          </Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Identificador institucional" hint="Matrícula o número de nómina">
            <Input value={form.institutional_id} onChange={set('institutional_id')} className="plate" />
          </Field>
          <Field label="Tipo" required>
            <Select value={form.person_type} onChange={set('person_type')}>
              {Object.entries(PERSON_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
          </Field>
        </div>
        <Field label="Correo electrónico" hint="Necesario para notificar incidencias.">
          <Input value={form.email} onChange={set('email')} type="email" />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Teléfono"><Input value={form.phone} onChange={set('phone')} /></Field>
          <Field label="Adscripción"><Input value={form.faculty} onChange={set('faculty')} /></Field>
        </div>
        {error && (
          <p className="rounded-lg px-3 py-2 text-[13px]"
            style={{ background: 'var(--deny-bg)', color: 'var(--deny)' }}>{error}</p>
        )}
      </div>
    </Modal>
  )
}

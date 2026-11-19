import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Plus, UserCog } from 'lucide-react'
import { ApiError, get, patch, post, qs } from '../lib/api'
import type { AppUser, Lane, Page } from '../lib/types'
import { ROLES, dateTime } from '../lib/format'
import {
  Badge, Button, Card, EmptyState, ErrorState, Field, Input, Modal, OriginBadge,
  Pagination, Select, SkeletonRows, Table, Td, Th, Tr,
} from '../components/ui'
import { PageHeader } from '../components/AdminLayout'
import { useAuth } from '../lib/auth'

export function Users() {
  const { user: me } = useAuth()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [creating, setCreating] = useState(false)
  const [resetting, setResetting] = useState<AppUser | null>(null)

  const query = useQuery({
    queryKey: ['users', page],
    queryFn: () => get<Page<AppUser>>(`/users${qs({ page, page_size: 25 })}`),
  })

  const lanes = useQuery({
    queryKey: ['lanes'], queryFn: () => get<Lane[]>('/catalog/lanes'), enabled: creating,
  })

  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) => post<AppUser>('/users', body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['users'] })
      setCreating(false)
    },
  })

  const toggleActive = useMutation({
    mutationFn: ({ id, is_active }: { id: number; is_active: boolean }) =>
      patch(`/users/${id}`, { is_active }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['users'] }),
  })

  const resetPassword = useMutation({
    mutationFn: ({ id, password }: { id: number; password: string }) =>
      post(`/users/${id}/password`, { new_password: password }),
    onSuccess: () => setResetting(null),
  })

  return (
    <>
      <PageHeader
        title="Usuarios"
        description="Cuentas con acceso a la plataforma y su rol operativo."
        actions={
          <Button variant="primary" icon={Plus} onClick={() => { create.reset(); setCreating(true) }}>
            Crear usuario
          </Button>
        }
      />

      <Card padded={false}>
        {query.isError ? (
          <ErrorState message={(query.error as Error).message} onRetry={() => void query.refetch()} />
        ) : query.isLoading ? (
          <SkeletonRows rows={6} cols={5} />
        ) : !query.data?.items.length ? (
          <EmptyState icon={UserCog} title="Sin usuarios" />
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th>Usuario</Th><Th>Rol</Th><Th>Carril asignado</Th>
                  <Th>Último acceso</Th><Th>Estado</Th><Th />
                </tr>
              </thead>
              <tbody>
                {query.data.items.map((u) => (
                  <Tr key={u.id}>
                    <Td>
                      <span className="font-medium">{u.full_name}</span>
                      <span className="plate block text-[12px]" style={{ color: 'var(--text-subtle)' }}>
                        {u.username}
                      </span>
                    </Td>
                    <Td>
                      <Badge tone={u.role === 'admin' ? 'info' : 'neutral'}>{ROLES[u.role] ?? u.role}</Badge>
                    </Td>
                    <Td>
                      <span style={{ color: 'var(--text-muted)' }}>{u.default_lane_name ?? '—'}</span>
                    </Td>
                    <Td className="num whitespace-nowrap text-[12.5px]">
                      {u.last_login_at ? dateTime(u.last_login_at) : 'Nunca'}
                    </Td>
                    <Td>
                      <div className="flex items-center gap-1.5">
                        {u.is_active ? <Badge tone="allow" dot>Activa</Badge> : <Badge tone="deny">Desactivada</Badge>}
                        {u.must_change_password && <Badge tone="review">Cambio pendiente</Badge>}
                        <OriginBadge origin={u.origin} />
                      </div>
                    </Td>
                    <Td>
                      <div className="flex justify-end gap-1.5">
                        <Button size="sm" variant="ghost" icon={KeyRound}
                          onClick={() => { resetPassword.reset(); setResetting(u) }}
                          aria-label="Restablecer contraseña" />
                        <Button size="sm" variant="ghost" disabled={u.id === me?.id}
                          onClick={() => toggleActive.mutate({ id: u.id, is_active: !u.is_active })}>
                          {u.is_active ? 'Desactivar' : 'Activar'}
                        </Button>
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

      <CreateUserModal
        open={creating}
        onClose={() => setCreating(false)}
        lanes={lanes.data ?? []}
        onSubmit={(b) => create.mutate(b)}
        pending={create.isPending}
        error={create.error instanceof ApiError ? create.error.message : null}
      />

      <ResetPasswordModal
        target={resetting}
        onClose={() => setResetting(null)}
        onSubmit={(password) => resetting && resetPassword.mutate({ id: resetting.id, password })}
        pending={resetPassword.isPending}
        error={resetPassword.error instanceof ApiError ? resetPassword.error.message : null}
      />
    </>
  )
}

function CreateUserModal({
  open, onClose, lanes, onSubmit, pending, error,
}: {
  open: boolean
  onClose: () => void
  lanes: Lane[]
  onSubmit: (b: Record<string, unknown>) => void
  pending: boolean
  error: string | null
}) {
  const [form, setForm] = useState({
    username: '', full_name: '', email: '', password: '', role: 'guard', default_lane_id: '',
  })
  const set = (k: string) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.target.value }))

  const valid = form.username.length >= 3 && form.full_name.length >= 3 && form.password.length >= 10

  return (
    <Modal
      open={open} onClose={onClose}
      title="Crear usuario"
      description="El usuario deberá cambiar su contraseña en el primer acceso."
      footer={
        <>
          <Button onClick={onClose}>Cancelar</Button>
          <Button variant="primary" loading={pending} disabled={!valid}
            onClick={() => onSubmit({
              ...form,
              email: form.email || null,
              default_lane_id: form.default_lane_id ? Number(form.default_lane_id) : null,
            })}>
            Crear
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Usuario" required hint="Letras, números, punto, guion.">
            <Input value={form.username} onChange={set('username')} className="plate" autoFocus />
          </Field>
          <Field label="Rol" required>
            <Select value={form.role} onChange={set('role')}>
              {Object.entries(ROLES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
          </Field>
        </div>
        <Field label="Nombre completo" required>
          <Input value={form.full_name} onChange={set('full_name')} />
        </Field>
        <Field label="Correo electrónico">
          <Input value={form.email} onChange={set('email')} type="email" />
        </Field>
        <Field label="Contraseña inicial" required hint="Mínimo 10 caracteres.">
          <Input value={form.password} onChange={set('password')} type="password" />
        </Field>
        {form.role === 'guard' && (
          <Field label="Carril asignado" hint="La consola de caseta abrirá en este carril.">
            <Select value={form.default_lane_id} onChange={set('default_lane_id')}>
              <option value="">Sin asignar</option>
              {lanes.map((l) => (
                <option key={l.id} value={l.id}>{l.name} · {l.access_point_name}</option>
              ))}
            </Select>
          </Field>
        )}
        {error && (
          <p className="rounded-lg px-3 py-2 text-[13px]"
            style={{ background: 'var(--deny-bg)', color: 'var(--deny)' }}>{error}</p>
        )}
      </div>
    </Modal>
  )
}

function ResetPasswordModal({
  target, onClose, onSubmit, pending, error,
}: {
  target: AppUser | null
  onClose: () => void
  onSubmit: (password: string) => void
  pending: boolean
  error: string | null
}) {
  const [password, setPassword] = useState('')
  return (
    <Modal
      open={target !== null}
      onClose={() => { setPassword(''); onClose() }}
      title="Restablecer contraseña"
      description={target ? `Cuenta: ${target.username}` : undefined}
      width="sm"
      footer={
        <>
          <Button onClick={() => { setPassword(''); onClose() }}>Cancelar</Button>
          <Button variant="primary" loading={pending} disabled={password.length < 10}
            onClick={() => { onSubmit(password); setPassword('') }}>
            Restablecer
          </Button>
        </>
      }
    >
      <Field label="Nueva contraseña" required hint="Mínimo 10 caracteres. El usuario deberá cambiarla al entrar.">
        <Input value={password} onChange={(e) => setPassword(e.target.value)} type="password" autoFocus />
      </Field>
      {error && (
        <p className="mt-3 rounded-lg px-3 py-2 text-[13px]"
          style={{ background: 'var(--deny-bg)', color: 'var(--deny)' }}>{error}</p>
      )}
    </Modal>
  )
}

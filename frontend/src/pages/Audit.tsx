import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ScrollText, Search } from 'lucide-react'
import { get, qs } from '../lib/api'
import type { AuditEntry, Page } from '../lib/types'
import { dateTime } from '../lib/format'
import {
  Badge, Button, Card, EmptyState, ErrorState, Input, Pagination, SkeletonRows,
  Table, Td, Th, Tr,
} from '../components/ui'
import { PageHeader } from '../components/AdminLayout'

export function Audit() {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [applied, setApplied] = useState('')

  const query = useQuery({
    queryKey: ['audit', page, applied],
    queryFn: () => get<Page<AuditEntry>>(`/audit${qs({ page, page_size: 30, action: applied })}`),
  })

  return (
    <>
      <PageHeader
        title="Auditoría"
        description="Registro append-only de las acciones con relevancia de seguridad. Las credenciales nunca se almacenan aquí."
      />

      <Card padded={false}>
        <div className="flex flex-wrap items-center gap-2 border-b p-4" style={{ borderColor: 'var(--border)' }}>
          <div className="relative min-w-[220px] flex-1 sm:max-w-[320px]">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2"
              style={{ color: 'var(--text-subtle)' }} />
            <Input value={search} onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { setApplied(search.trim()); setPage(1) } }}
              placeholder="Filtrar por acción (auth, events, registry…)" className="pl-9" />
          </div>
          <Button onClick={() => { setApplied(search.trim()); setPage(1) }}>Filtrar</Button>
          <span className="ml-auto text-[12.5px]" style={{ color: 'var(--text-subtle)' }}>
            {query.data ? `${query.data.total.toLocaleString('es-MX')} registros` : ''}
          </span>
        </div>

        {query.isError ? (
          <ErrorState message={(query.error as Error).message} onRetry={() => void query.refetch()} />
        ) : query.isLoading ? (
          <SkeletonRows rows={10} cols={5} />
        ) : !query.data?.items.length ? (
          <EmptyState icon={ScrollText} title="Sin registros de auditoría" />
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th>Fecha</Th><Th>Actor</Th><Th>Acción</Th>
                  <Th>Entidad</Th><Th>Detalle</Th><Th>Origen IP</Th>
                </tr>
              </thead>
              <tbody>
                {query.data.items.map((a) => (
                  <Tr key={a.id}>
                    <Td className="num whitespace-nowrap">{dateTime(a.at)}</Td>
                    <Td>
                      <span className="block max-w-[150px] truncate" title={a.actor_label}>{a.actor_label}</span>
                    </Td>
                    <Td><code className="plate text-[11.5px]">{a.action}</code></Td>
                    <Td>
                      {a.entity_type ? (
                        <Badge tone="neutral">
                          {a.entity_type}{a.entity_id ? ` #${a.entity_id}` : ''}
                        </Badge>
                      ) : '—'}
                    </Td>
                    <Td>
                      <span className="block max-w-[210px] truncate" title={a.summary ?? undefined}
                        style={{ color: 'var(--text-muted)' }}>
                        {a.summary ?? '—'}
                      </span>
                    </Td>
                    <Td className="num text-[12px]" >{a.ip_address ?? '—'}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
            <Pagination page={page} pageSize={query.data.page_size} total={query.data.total} onPage={setPage} />
          </>
        )}
      </Card>
    </>
  )
}

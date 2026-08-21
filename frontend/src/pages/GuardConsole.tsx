import { useCallback, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, Ban, Camera, CheckCircle2, ChevronRight, Clock, ImageUp,
  Keyboard, LogOut, Moon, ShieldCheck, ShieldX, Sun, Upload, UserRound,
} from 'lucide-react'
import { get, post, upload } from '../lib/api'
import type { AccessEvent, CaptureResponse, Page } from '../lib/types'
import { ACTUATION, PERSON_TYPES, percent, relative, timeOnly } from '../lib/format'
import {
  Badge, Button, Card, EmptyState, Field, Input, Plate, Spinner, Textarea,
  cx, decisionTone,
} from '../components/ui'
import { Logo } from '../components/Brand'
import { useAuth } from '../lib/auth'
import { useTheme } from '../lib/theme'
import { ApiError } from '../lib/api'

/**
 * The lane console.
 *
 * Optimised for a booth: one dominant result panel that answers "does this
 * car go through?" from across the room, with everything else subordinate.
 */
export function GuardConsole({ embedded = false }: { embedded?: boolean }) {
  const { user, logout } = useAuth()
  const { theme, toggle } = useTheme()
  const queryClient = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)

  const [result, setResult] = useState<CaptureResponse | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [manualPlate, setManualPlate] = useState('')
  const [notes, setNotes] = useState('')
  const [dragging, setDragging] = useState(false)

  const laneId = user?.default_lane?.id

  const pending = useQuery({
    queryKey: ['events', 'pending', laneId],
    queryFn: () => get<AccessEvent[]>(`/events/pending?limit=8${laneId ? `&lane_id=${laneId}` : ''}`),
    refetchInterval: 15_000,
  })

  const recent = useQuery({
    queryKey: ['events', 'lane-recent', laneId],
    queryFn: () =>
      get<Page<AccessEvent>>(`/events?page_size=6${laneId ? `&lane_id=${laneId}` : ''}`),
    refetchInterval: 15_000,
  })

  const invalidate = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['events'] })
  }, [queryClient])

  const captureMutation = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData()
      form.append('image', file)
      if (laneId) form.append('lane_id', String(laneId))
      return upload<CaptureResponse>('/events/capture', form)
    },
    onSuccess: (data) => {
      setResult(data)
      setError(null)
      invalidate()
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Error al procesar la imagen.'),
  })

  const manualMutation = useMutation({
    mutationFn: (plate: string) =>
      post<CaptureResponse>('/events/manual', { plate, lane_id: laneId, notes: notes || null }),
    onSuccess: (data) => {
      setResult(data)
      setError(null)
      setManualPlate('')
      setNotes('')
      setPreview(null)
      invalidate()
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Error al registrar la placa.'),
  })

  const decideMutation = useMutation({
    mutationFn: ({ id, decision }: { id: number; decision: 'allow' | 'deny' }) =>
      post<AccessEvent>(`/events/${id}/decision`, {
        decision, notes: notes || null, open_barrier: decision === 'allow',
      }),
    onSuccess: (event) => {
      setResult((prev) => (prev ? { ...prev, event: { ...prev.event, ...event } } : prev))
      setNotes('')
      invalidate()
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Error al registrar la decisión.'),
  })

  function handleFile(file: File | null | undefined) {
    if (!file) return
    setPreview(URL.createObjectURL(file))
    setResult(null)
    captureMutation.mutate(file)
  }

  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview) }, [preview])

  const busy = captureMutation.isPending || manualMutation.isPending
  const event = result?.event

  const content = (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(300px,0.85fr)]">
      {/* ---- Main capture + decision column ---- */}
      <div className="space-y-4">
        {/* Capture */}
        <Card padded={false} className="overflow-hidden">
          <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragging(false)
              handleFile(e.dataTransfer.files?.[0])
            }}
            className={cx(
              'relative flex min-h-[268px] flex-col items-center justify-center p-6 transition-colors',
              dragging && 'bg-[var(--surface-sunken)]',
            )}
          >
            {preview && !busy ? (
              <div className="w-full">
                <img
                  src={preview}
                  alt="Captura del carril"
                  className="mx-auto max-h-[300px] w-auto rounded-lg border object-contain"
                  style={{ borderColor: 'var(--border)' }}
                />
              </div>
            ) : busy ? (
              <div className="flex flex-col items-center gap-3">
                <span className="pulse-ring grid h-14 w-14 place-items-center rounded-full"
                  style={{ background: 'var(--allow-bg)', color: 'var(--allow)' }}>
                  <Camera size={22} />
                </span>
                <p className="text-[14px] font-medium">Analizando imagen…</p>
                <p className="text-[12.5px]" style={{ color: 'var(--text-subtle)' }}>
                  Detección de vehículo y lectura de placa
                </p>
              </div>
            ) : (
              <div className="flex flex-col items-center text-center">
                <span className="mb-3 grid h-14 w-14 place-items-center rounded-2xl"
                  style={{ background: 'var(--surface-sunken)', color: 'var(--text-subtle)' }}>
                  <ImageUp size={24} />
                </span>
                <p className="text-[15px] font-semibold">Capturar vehículo</p>
                <p className="mt-1 max-w-xs text-[13px]" style={{ color: 'var(--text-muted)' }}>
                  Arrastra una imagen aquí o selecciónala para procesar el acceso.
                </p>
                <Button className="mt-4" variant="primary" icon={Upload}
                  onClick={() => fileRef.current?.click()}>
                  Seleccionar imagen
                </Button>
              </div>
            )}
            <input
              ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp"
              className="sr-only"
              onChange={(e) => handleFile(e.target.files?.[0])}
            />
          </div>

          {preview && !busy && (
            <div className="flex justify-center gap-2 border-t px-4 py-3"
              style={{ borderColor: 'var(--border)', background: 'var(--surface-sunken)' }}>
              <Button size="sm" icon={Upload} onClick={() => fileRef.current?.click()}>
                Nueva captura
              </Button>
            </div>
          )}
        </Card>

        {error && (
          <div role="alert" className="flex items-start gap-2 rounded-lg px-4 py-3 text-[13px]"
            style={{ background: 'var(--deny-bg)', color: 'var(--deny)' }}>
            <AlertTriangle size={16} className="mt-px shrink-0" />
            {error}
          </div>
        )}

        {/* Decision */}
        {event && (
          <DecisionPanel
            result={result!}
            notes={notes}
            onNotes={setNotes}
            onDecide={(decision) => decideMutation.mutate({ id: event.id, decision })}
            deciding={decideMutation.isPending}
          />
        )}

        {/* Manual entry */}
        <Card>
          <div className="mb-3 flex items-center gap-2">
            <Keyboard size={16} style={{ color: 'var(--text-muted)' }} />
            <h2 className="text-[14px] font-semibold">Captura manual de placa</h2>
          </div>
          <p className="mb-3 text-[12.5px]" style={{ color: 'var(--text-muted)' }}>
            Úsala cuando la cámara no esté disponible o la placa no sea legible en la imagen.
          </p>
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={(e) => { e.preventDefault(); if (manualPlate.trim()) manualMutation.mutate(manualPlate.trim()) }}
          >
            <div className="min-w-[180px] flex-1">
              <Field label="Placa">
                <Input
                  value={manualPlate}
                  onChange={(e) => setManualPlate(e.target.value.toUpperCase())}
                  placeholder="GPT-45-67"
                  className="plate uppercase"
                  maxLength={20}
                />
              </Field>
            </div>
            <Button type="submit" variant="primary" loading={manualMutation.isPending}
              disabled={!manualPlate.trim() || !laneId}>
              Consultar
            </Button>
          </form>
          {!laneId && (
            <p className="mt-2 text-[12px]" style={{ color: 'var(--review)' }}>
              No tienes un carril asignado. Solicita al administrador que te asigne uno.
            </p>
          )}
        </Card>
      </div>

      {/* ---- Side column ---- */}
      <div className="space-y-4">
        <Card>
          <h2 className="mb-3 flex items-center gap-2 text-[14px] font-semibold">
            <Clock size={15} style={{ color: 'var(--review)' }} />
            Pendientes de revisión
            {!!pending.data?.length && (
              <Badge tone="review" className="ml-auto">{pending.data.length}</Badge>
            )}
          </h2>
          {pending.isLoading ? (
            <Spinner />
          ) : !pending.data?.length ? (
            <p className="py-4 text-center text-[13px]" style={{ color: 'var(--text-subtle)' }}>
              Sin eventos pendientes.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {pending.data.map((e) => (
                <li key={e.id}>
                  <button
                    onClick={() => {
                      setPreview(null)
                      setResult({
                        event: { ...e, detections: [], plate_candidates: [] },
                        recognition_backend: '',
                      } as CaptureResponse)
                    }}
                    className="flex w-full items-center gap-2.5 rounded-lg border px-2.5 py-2 text-left transition-colors hover:bg-[var(--surface-sunken)]"
                    style={{ borderColor: 'var(--border)' }}
                  >
                    <Plate value={e.plate_display} size="sm" muted />
                    <span className="min-w-0 flex-1 truncate text-[12px]"
                      style={{ color: 'var(--text-muted)' }}>
                      {e.reason_label}
                    </span>
                    <ChevronRight size={14} style={{ color: 'var(--text-subtle)' }} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <h2 className="mb-3 text-[14px] font-semibold">Últimos vehículos</h2>
          {recent.isLoading ? (
            <Spinner />
          ) : !recent.data?.items.length ? (
            <EmptyState icon={Camera} title="Sin registros" />
          ) : (
            <ul className="space-y-2">
              {recent.data.items.map((e) => (
                <li key={e.id} className="flex items-center gap-2.5">
                  <span className="h-7 w-1 shrink-0 rounded-full"
                    style={{
                      background: e.decision === 'allow' ? 'var(--allow)'
                        : e.decision === 'deny' ? 'var(--deny)' : 'var(--review)',
                    }} />
                  <Plate value={e.plate_display} size="sm" muted />
                  <span className="num ml-auto shrink-0 text-[11.5px]"
                    style={{ color: 'var(--text-subtle)' }}>
                    {timeOnly(e.occurred_at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  )

  if (embedded) return content

  return (
    <div className="min-h-screen">
      <header
        className="sticky top-0 z-30 border-b"
        style={{ background: 'var(--surface-raised)', borderColor: 'var(--border)' }}
      >
        <div className="mx-auto flex h-16 max-w-[1500px] items-center gap-4 px-4 sm:px-6">
          <Logo size={30} />
          <div className="min-w-0">
            <p className="text-[15px] font-bold leading-none tracking-tight">Consola de caseta</p>
            <p className="mt-1 truncate text-[12px]" style={{ color: 'var(--text-subtle)' }}>
              {user?.default_lane
                ? `${user.default_lane.name} · ${user.default_lane.access_point_name} · ${user.default_lane.campus_name}`
                : 'Sin carril asignado'}
            </p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Badge tone="allow" dot className="hidden sm:inline-flex">En turno</Badge>
            <span className="hidden text-[13px] font-medium sm:inline">{user?.full_name}</span>
            <Button size="sm" variant="ghost" icon={theme === 'dark' ? Sun : Moon}
              onClick={toggle} aria-label="Cambiar tema" />
            <Button size="sm" variant="ghost" icon={LogOut} onClick={() => void logout()}>
              <span className="hidden sm:inline">Salir</span>
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1500px] p-4 sm:p-6">{content}</main>

      <footer className="mx-auto max-w-[1500px] px-4 pb-6 text-center sm:px-6">
        <p className="text-[11px]" style={{ color: 'var(--text-subtle)' }}>
          Entorno de demostración — los registros no corresponden a datos reales de la
          Universidad de Colima.
        </p>
      </footer>
    </div>
  )
}

/* ------------------------------------------------------------------------ */
function DecisionPanel({
  result, notes, onNotes, onDecide, deciding,
}: {
  result: CaptureResponse
  notes: string
  onNotes: (v: string) => void
  onDecide: (d: 'allow' | 'deny') => void
  deciding: boolean
}) {
  const e = result.event
  const tone = decisionTone(e.decision)
  const VERDICT = {
    allow: { fg: 'var(--allow)', bg: 'var(--allow-bg)', Icon: ShieldCheck },
    deny: { fg: 'var(--deny)', bg: 'var(--deny-bg)', Icon: ShieldX },
    review: { fg: 'var(--review)', bg: 'var(--review-bg)', Icon: AlertTriangle },
  } as const
  // decisionTone only ever yields these three for an event decision.
  const colors = VERDICT[tone as keyof typeof VERDICT] ?? VERDICT.review

  const needsDecision = e.decision === 'manual_review' && !e.resolved_at

  return (
    <Card padded={false} className="animate-in-up overflow-hidden">
      {/* Verdict banner — readable across a booth */}
      <div className="flex items-center gap-4 px-5 py-4" style={{ background: colors.bg }}>
        <colors.Icon size={30} style={{ color: colors.fg }} className="shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="text-[21px] font-bold leading-tight tracking-tight" style={{ color: colors.fg }}>
            {e.decision_label}
          </p>
          <p className="mt-0.5 text-[13px]" style={{ color: colors.fg, opacity: 0.85 }}>
            {e.reason_label}
            {e.is_manual && ' · resuelto manualmente'}
          </p>
        </div>
        <div className="hidden shrink-0 text-right sm:block">
          <Plate value={e.plate_display} size="lg" />
          {e.plate_confidence != null && (
            <p className="num mt-1.5 text-[11.5px]" style={{ color: colors.fg, opacity: 0.8 }}>
              Confianza {percent(e.plate_confidence)}
            </p>
          )}
        </div>
      </div>

      <div className="px-5 py-4 sm:hidden">
        <Plate value={e.plate_display} size="lg" />
      </div>

      {/* Identity */}
      <div className="grid gap-px sm:grid-cols-2" style={{ background: 'var(--border)' }}>
        <div className="p-5" style={{ background: 'var(--surface-raised)' }}>
          <p className="mb-2.5 text-[11px] font-semibold uppercase tracking-wider"
            style={{ color: 'var(--text-subtle)' }}>
            Identidad
          </p>
          {result.matched_person ? (
            <div className="flex items-start gap-3">
              <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-[15px] font-bold text-white"
                style={{ background: 'var(--color-uc-600)' }}>
                {result.matched_person.full_name.slice(0, 1)}
              </span>
              <div className="min-w-0">
                <p className="text-[14.5px] font-semibold leading-tight">
                  {result.matched_person.full_name}
                </p>
                <p className="mt-1 text-[12.5px]" style={{ color: 'var(--text-muted)' }}>
                  {PERSON_TYPES[result.matched_person.person_type] ?? result.matched_person.person_type}
                  {result.matched_person.institutional_id && ` · ${result.matched_person.institutional_id}`}
                </p>
                {result.matched_person.faculty && (
                  <p className="mt-0.5 text-[12px]" style={{ color: 'var(--text-subtle)' }}>
                    {result.matched_person.faculty}
                  </p>
                )}
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-2.5 text-[13px]" style={{ color: 'var(--text-subtle)' }}>
              <UserRound size={18} />
              Sin identidad asociada a esta placa
            </div>
          )}
        </div>

        <div className="p-5" style={{ background: 'var(--surface-raised)' }}>
          <p className="mb-2.5 text-[11px] font-semibold uppercase tracking-wider"
            style={{ color: 'var(--text-subtle)' }}>
            Vehículo y permiso
          </p>
          {result.matched_vehicle ? (
            <>
              <p className="text-[14px] font-medium">
                {[result.matched_vehicle.brand, result.matched_vehicle.model].filter(Boolean).join(' ') || 'Vehículo registrado'}
              </p>
              <p className="mt-1 text-[12.5px]" style={{ color: 'var(--text-muted)' }}>
                {[result.matched_vehicle.color, result.matched_vehicle.year].filter(Boolean).join(' · ')}
              </p>
            </>
          ) : (
            <p className="text-[13px]" style={{ color: 'var(--text-subtle)' }}>
              Vehículo no registrado en el padrón
            </p>
          )}
          {result.active_authorization && (
            <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
              <Badge tone="allow">Permiso {result.active_authorization.permit_code}</Badge>
              {result.active_authorization.valid_until && (
                <span className="text-[11.5px]" style={{ color: 'var(--text-subtle)' }}>
                  vence {new Date(result.active_authorization.valid_until).toLocaleDateString('es-MX')}
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Alternative readings — makes the OCR auditable instead of opaque */}
      {e.plate_candidates?.length > 1 && (
        <div className="border-t px-5 py-3" style={{ borderColor: 'var(--border)' }}>
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider"
            style={{ color: 'var(--text-subtle)' }}>
            Otras lecturas posibles
          </p>
          <div className="flex flex-wrap gap-1.5">
            {e.plate_candidates.slice(0, 5).map((c, i) => (
              <span key={`${c.plate}-${i}`}
                className="plate rounded border px-1.5 py-0.5 text-[11.5px]"
                style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
                title={`${c.layout}${c.repaired ? ' · corregida' : ''} · ${Math.round(c.score * 100)}%`}>
                {c.display} <span className="num opacity-60">{Math.round(c.score * 100)}%</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="border-t px-5 py-4" style={{ borderColor: 'var(--border)', background: 'var(--surface-sunken)' }}>
        {needsDecision ? (
          <>
            <Textarea
              value={notes}
              onChange={(ev) => onNotes(ev.target.value)}
              placeholder="Motivo o nota de la decisión (opcional)…"
              className="mb-3"
            />
            <div className="flex flex-wrap gap-2">
              <Button variant="success" size="lg" icon={CheckCircle2} loading={deciding}
                onClick={() => onDecide('allow')} className="flex-1">
                Permitir acceso
              </Button>
              <Button variant="danger" size="lg" icon={Ban} loading={deciding}
                onClick={() => onDecide('deny')} className="flex-1">
                Denegar acceso
              </Button>
            </div>
          </>
        ) : (
          <div className="flex flex-wrap items-center justify-between gap-2 text-[12.5px]"
            style={{ color: 'var(--text-muted)' }}>
            <span className="flex items-center gap-1.5">
              {e.actuation_status === 'simulated' && <AlertTriangle size={14} style={{ color: 'var(--review)' }} />}
              {ACTUATION[e.actuation_status] ?? e.actuation_status}
              {e.decided_by_name && ` · por ${e.decided_by_name}`}
            </span>
            <span className="num">{relative(e.occurred_at)}</span>
          </div>
        )}
      </div>
    </Card>
  )
}

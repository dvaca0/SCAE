import { useCallback, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, Ban, Camera, CheckCircle2, ChevronRight, Clock,
  Keyboard, LogOut, Moon, ScanSearch, ShieldCheck, ShieldX, Sun, UserPlus,
  UserRound,
} from 'lucide-react'
import { get, post } from '../lib/api'
import type {
  AccessEvent, AccessEventDetail, CaptureResponse, Page,
} from '../lib/types'
import { ACTUATION, PERSON_TYPES, percent, relative, timeOnly } from '../lib/format'
import {
  Badge, Button, Card, EmptyState, Field, Input, Plate, Spinner, Textarea,
  decisionTone,
} from '../components/ui'
import { Logo } from '../components/Brand'
// PARCHE LOCAL (Raspberry Pi 5) - vista en vivo. Ver /opt/scae/PATCHES.md
import { CameraPanel } from '../components/CameraPanel'
import { IntakeDialog, IntakeSummary } from '../components/IntakeDialog'
import type { IntakeResponse } from '../components/IntakeDialog'
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

  const [result, setResult] = useState<CaptureResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [manualPlate, setManualPlate] = useState('')
  const [notes, setNotes] = useState('')
  // Correccion de la placa al resolver. La API ya aceptaba `corrected_plate`,
  // pero la consola no lo ofrecia: el guardia veia una lectura mal y solo podia
  // permitir o denegar con ella.
  const [correctedPlate, setCorrectedPlate] = useState('')
  // Alta de un vehiculo desconocido: visitante o credencial.
  const [intakeOpen, setIntakeOpen] = useState(false)
  const [intakeDone, setIntakeDone] = useState<IntakeResponse | null>(null)
  // Ultimo evento que la consola ya atendio sola, para no repetirse.
  const seenEventId = useRef<number | null>(null)
  const [arrival, setArrival] = useState<CaptureResponse | null>(null)

  const laneId = user?.default_lane?.id
  // Operadores: administrador, supervisor y guardia (igual que el backend).
  const canWatchCamera =
    user?.role === 'admin' || user?.role === 'supervisor' || user?.role === 'guard'

  const pending = useQuery({
    queryKey: ['events', 'pending', laneId],
    queryFn: () => get<AccessEvent[]>(`/events/pending?limit=8${laneId ? `&lane_id=${laneId}` : ''}`),
    refetchInterval: 15_000,
  })

  /**
   * El disparador del pipeline.
   *
   * PARCHE LOCAL (Raspberry Pi 5) - ver /opt/scae/PATCHES.md
   *
   * Sin esto, una deteccion automatica no llegaba a la consola: el guardia
   * tenia que fijarse en la cola de pendientes -que ademas se refrescaba cada
   * 15 s- y hacer clic. En una caseta eso no sirve: cuando el guardia mira la
   * pantalla, el coche ya esta delante de la pluma.
   *
   * Se consulta el ultimo evento del carril cada 3 s y, en cuanto cambia, la
   * consola se planta sola en el panel de decision. Si el vehiculo no esta en
   * el padron, ademas abre el alta. La peticion ya trae la identidad resuelta,
   * asi que no hacen falta dos viajes.
   */
  const latest = useQuery({
    queryKey: ['events', 'lane-latest', laneId],
    queryFn: () => get<CaptureResponse>(`/events/lane/${laneId}/latest`),
    enabled: !!laneId,
    // 700 ms, no 3 s. Es una consulta barata -un SELECT por carril con LIMIT 1-
    // y esos 3 s se sumaban enteros al tiempo que el guardia espera a que
    // aparezca el aviso, con el coche ya parado delante de la pluma. A 700 ms el
    // sondeo deja de ser el tramo que manda.
    refetchInterval: 700,
    // 404 mientras el carril no tenga ningun evento: no es un error que
    // merezca reintentos.
    retry: false,
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

  /** Abre un evento de la cola con su contexto real (identidad incluida). */
  const openEvent = useCallback(async (id: number) => {
    setIntakeDone(null)
    setError(null)
    try {
      // Antes se fabricaba aqui un CaptureResponse con la identidad vacia, y
      // por eso un vehiculo YA REGISTRADO se ofrecia como desconocido.
      const data = await get<CaptureResponse>(`/events/${id}/context`)
      seenEventId.current = data.event.id
      setResult(data)
      setCorrectedPlate(data.event.plate_display ?? '')
      // Abrir también el alta al sacar un evento de la cola, por la misma
      // razón: si el vehículo no está en el padrón, registrarlo es la acción.
      if (data.event.decision === 'manual_review' && !data.event.resolved_at
          && !data.matched_vehicle) {
        setIntakeOpen(true)
      }
    } catch {
      setError('No se pudo abrir el evento.')
    }
  }, [])

  /**
   * Analizar el carril AHORA.
   *
   * PARCHE LOCAL (Raspberry Pi 5) — ver /opt/scae/PATCHES.md
   *
   * Recorre el mismo pipeline que una captura automática, pero disparado por el
   * guardia. El vigilante decide cuándo mirar —puerta de movimiento, esperar a
   * que el carril se despeje, exigir alguna lectura— y eso dejaba al guardia sin
   * forma de decir «mira ahora» con un coche delante y el sistema callado.
   *
   * No es una simulación: si la placa está en el padrón, esto abre la barrera.
   */
  const captureNow = useMutation({
    mutationFn: () =>
      post<CaptureResponse>(
        `/camera/capture-now${laneId ? `?lane_id=${laneId}` : ''}`, {},
      ),
    onSuccess: (data) => {
      setError(null)
      setIntakeDone(null)
      setResult(data)
      setArrival(data)
      setCorrectedPlate(data.event.plate_display ?? '')
      // Que no lo vuelva a disparar el sondeo del último evento.
      seenEventId.current = data.event.id
      const e = data.event
      // Vehículo fuera del padrón: es justo el prompt que se quiere ver.
      if (e.decision === 'manual_review' && !e.resolved_at && !data.matched_vehicle) {
        setIntakeOpen(true)
      }
      invalidate()
    },
    onError: (e) =>
      setError(e instanceof ApiError ? e.message : 'No se pudo analizar el carril.'),
  })

  const manualMutation = useMutation({
    mutationFn: (plate: string) =>
      post<CaptureResponse>('/events/manual', { plate, lane_id: laneId, notes: notes || null }),
    onSuccess: (data) => {
      setResult(data)
      setError(null)
      setManualPlate('')
      setNotes('')
      setIntakeDone(null)
      // Una placa tecleada sigue el MISMO pipeline que una reconocida: si no
      // esta en el padron, el alta se abre sola en vez de esperar otro clic.
      seenEventId.current = data.event.id
      const e = data.event
      if (e.decision === 'manual_review' && !e.resolved_at &&
          !data.matched_vehicle && e.plate_normalized) {
        setIntakeOpen(true)
      }
      invalidate()
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Error al registrar la placa.'),
  })

  const decideMutation = useMutation({
    mutationFn: ({ id, decision, plate }: {
      id: number; decision: 'allow' | 'deny'; plate?: string
    }) =>
      post<AccessEvent>(`/events/${id}/decision`, {
        decision, notes: notes || null, open_barrier: decision === 'allow',
        // `plate` gana cuando se da paso con un registro existente.
        corrected_plate: (plate ?? correctedPlate).trim() || null,
      }),
    onSuccess: (event) => {
      setResult((prev) => (prev ? { ...prev, event: { ...prev.event, ...event } } : prev))
      setNotes('')
      setCorrectedPlate('')
      invalidate()
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Error al registrar la decisión.'),
  })

  useEffect(() => {
    const data = latest.data
    if (!data) return
    const id = data.event.id

    // Primera carga: tomar nota y no disparar nada. Si no, abrir la consola
    // levantaria el panel -y puede que el alta- por un coche que paso hace
    // horas.
    if (seenEventId.current === null) {
      seenEventId.current = id
      return
    }
    if (id === seenEventId.current) return

    // El guardia esta tecleando un alta: no se le pisa la pantalla. El evento
    // NO se marca como visto, asi que se atendera en cuanto cierre el dialogo.
    if (intakeOpen) return

    seenEventId.current = id
    setResult(data)
    setIntakeDone(null)
    setError(null)
    setArrival(data)
    setCorrectedPlate(data.event.plate_display ?? '')

    const e = data.event
    // El alta se abre SIEMPRE que el vehículo no esté en el padrón, haya placa
    // legible o no.
    //
    // Antes se exigía `plate_normalized`, y eso dejaba fuera justo el caso más
    // frecuente con una placa difícil: el OCR no saca ninguna lectura válida,
    // el evento llega sin placa, y el guardia solo veía «Permitir / Denegar».
    // Tenía un coche delante al que no podía dar de alta. Ahora se abre el
    // formulario y él teclea la placa, que es lo único que faltaba.
    const needsGuard = e.decision === 'manual_review' && !e.resolved_at
    if (needsGuard && !data.matched_vehicle) setIntakeOpen(true)
  }, [latest.data, intakeOpen])

  // El aviso de llegada se apaga solo: es para que el guardia levante la
  // vista, no para quedarse en pantalla.
  useEffect(() => {
    if (!arrival) return
    const t = setTimeout(() => setArrival(null), 6_000)
    return () => clearTimeout(t)
  }, [arrival])

  /** El alta terminó: el evento ya viene resuelto y la barrera accionada. */
  function handleIntakeDone(data: IntakeResponse) {
    setIntakeOpen(false)
    setIntakeDone(data)
    setResult((prev) => (prev ? { ...prev, event: data.event } : prev))
    invalidate()
    void queryClient.invalidateQueries({ queryKey: ['vehicles'] })
    void queryClient.invalidateQueries({ queryKey: ['people'] })
  }

  const event = result?.event

  const content = (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(300px,0.85fr)]">
      {/* ---- Main capture + decision column ---- */}
      <div className="space-y-4">
        {/* PARCHE LOCAL (Raspberry Pi 5): vista en vivo de la camara.
            Se oculta al auditor: su rol es de solo lectura sobre el registro, y
            el backend le deniega /camera/* igualmente, asi que mostrarle el
            panel solo le ensenaria un error. */}
        {canWatchCamera && <CameraPanel />}

        {/* PARCHE LOCAL (Raspberry Pi 5): la carga manual de imagen se retiro.
            El carril se reconoce en tiempo real sobre el video en vivo, asi que
            subir una foto a mano ya no es la via de entrada: era un resto de
            cuando no habia camara. La captura por placa tecleada SI se queda,
            porque sigue haciendo falta con una placa ilegible o una moto. */}

        {canWatchCamera && (
          <Card>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <h2 className="flex items-center gap-2 text-[14px] font-semibold">
                  <ScanSearch size={16} style={{ color: 'var(--text-muted)' }} />
                  Analizar el carril ahora
                </h2>
                <p className="mt-1 text-[12.5px]" style={{ color: 'var(--text-muted)' }}>
                  Captura un cuadro y lo pasa por el reconocimiento completo. Si el
                  vehículo no está en el padrón, abre el alta. Si lo está y la
                  lectura es buena, <strong>abre la barrera de verdad</strong>.
                </p>
              </div>
              <Button
                variant="primary"
                icon={ScanSearch}
                loading={captureNow.isPending}
                disabled={!laneId}
                onClick={() => captureNow.mutate()}
              >
                Analizar ahora
              </Button>
            </div>
            {!laneId && (
              <p className="mt-2 text-[12px]" style={{ color: 'var(--review)' }}>
                Necesitas un carril asignado.
              </p>
            )}
          </Card>
        )}

        {arrival && <ArrivalBanner result={arrival} />}

        {intakeDone && <IntakeSummary result={intakeDone} />}

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
            onRegister={() => setIntakeOpen(true)}
            plate={correctedPlate}
            onPlate={setCorrectedPlate}
          />
        )}

        {/* Manual entry */}
        <Card>
          <div className="mb-3 flex items-center gap-2">
            <Keyboard size={16} style={{ color: 'var(--text-muted)' }} />
            <h2 className="text-[14px] font-semibold">Captura manual de placa</h2>
          </div>
          <p className="mb-3 text-[12.5px]" style={{ color: 'var(--text-muted)' }}>
            Úsala cuando la placa no sea legible para la cámara, con una moto o con una
            placa dañada. Si no está en el padrón, podrás registrarla igual que con el
            reconocimiento automático.
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
                  placeholder="Teclea la placa"
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
                    onClick={() => { void openEvent(e.id) }}
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

  const dialogs = (
    <IntakeDialog
      event={(event as AccessEventDetail) ?? null}
      open={intakeOpen}
      onClose={() => setIntakeOpen(false)}
      onDone={handleIntakeDone}
      similar={result?.similar_vehicles ?? []}
      onUseExisting={(plate) => {
        // Es un coche que YA está en el padrón, leído de otra forma. Se
        // resuelve el evento con su placa: así queda asociado al titular
        // correcto y no se crea un duplicado.
        if (!event) return
        setIntakeOpen(false)
        setCorrectedPlate(plate)
        decideMutation.mutate({ id: event.id, decision: 'allow', plate })
      }}
    />
  )

  if (embedded) return <>{content}{dialogs}</>

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

      {dialogs}

      <footer className="mx-auto max-w-[1500px] px-4 pb-6 text-center sm:px-6">
        <p className="text-[11px]" style={{ color: 'var(--text-subtle)' }}>
          Entorno de demostración — los registros no corresponden a datos reales de la
          Universidad de Colima.
        </p>
      </footer>
    </div>
  )
}

/**
 * Aviso de que acaba de pasar algo en el carril.
 *
 * PARCHE LOCAL (Raspberry Pi 5). El panel de decision ya dice QUE se decidio,
 * pero aparece sin mas, y en una caseta el guardia no esta mirando la pantalla
 * todo el rato. Esto es lo que le hace levantar la vista: una franja que entra
 * animada, dice la placa y el veredicto de un vistazo, y se apaga sola a los
 * seis segundos para no quedarse ahi mintiendo sobre el coche de hace un rato.
 */
function ArrivalBanner({ result }: { result: CaptureResponse }) {
  const e = result.event
  const tone = decisionTone(e.decision)
  const COLORS = {
    allow: { fg: 'var(--allow)', bg: 'var(--allow-bg)', Icon: ShieldCheck,
             text: 'Acceso permitido' },
    deny: { fg: 'var(--deny)', bg: 'var(--deny-bg)', Icon: ShieldX,
            text: 'Acceso denegado' },
    review: { fg: 'var(--review)', bg: 'var(--review-bg)', Icon: AlertTriangle,
              text: 'Requiere tu decision' },
  } as const
  const c = COLORS[tone as keyof typeof COLORS] ?? COLORS.review

  return (
    <div
      className="animate-in-up flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg px-4 py-3"
      style={{ background: c.bg, color: c.fg }}
      role="status"
      aria-live="polite"
    >
      <span className="pulse-ring grid h-8 w-8 shrink-0 place-items-center rounded-full"
        style={{ background: c.fg, color: c.bg }}>
        <c.Icon size={17} />
      </span>
      <span className="text-[14px] font-bold">Vehiculo en el carril</span>
      <Plate value={e.plate_display} size="sm" />
      <span className="text-[13px] font-semibold">{c.text}</span>
      <span className="text-[12.5px]" style={{ opacity: 0.85 }}>
        {e.reason_label}
      </span>
      <span className="num ml-auto text-[12px]" style={{ opacity: 0.75 }}>
        {timeOnly(e.occurred_at)}
      </span>
    </div>
  )
}

/* ------------------------------------------------------------------------ */
function DecisionPanel({
  result, notes, onNotes, onDecide, deciding, onRegister, plate, onPlate,
}: {
  result: CaptureResponse
  notes: string
  onNotes: (v: string) => void
  onDecide: (d: 'allow' | 'deny') => void
  deciding: boolean
  /** Abre el alta de visitante / credencial. */
  onRegister: () => void
  /** Placa corregida a mano; se envía con la decisión. */
  plate: string
  onPlate: (v: string) => void
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
  // Un vehículo fuera del padrón es el caso que el alta resuelve, **con placa
  // legible o sin ella**: si el OCR no pudo leerla, el guardia la teclea en el
  // formulario. Exigir `plate_normalized` aquí dejaba al guardia con un coche
  // delante y solo los botones de permitir o denegar.
  const isUnknownVehicle = needsDecision && !result.matched_vehicle

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
            {/* Corrección de la placa antes de decidir. Si el OCR leyó mal,
                permitir o denegar con esa lectura deja el registro equivocado
                para siempre. */}
            <div className="mb-3 flex flex-wrap items-end gap-2">
              <div className="min-w-[170px] flex-1">
                <Field
                  label="Placa"
                  hint={
                    plate.trim().toUpperCase() !==
                    (e.plate_display ?? '').toUpperCase()
                      ? 'Corregida respecto a lo que leyó la cámara.'
                      : undefined
                  }
                >
                  <Input
                    value={plate}
                    onChange={(ev) => onPlate(ev.target.value.toUpperCase())}
                    placeholder={e.plate_display ?? 'Teclea la placa'}
                    className="plate uppercase"
                    maxLength={20}
                  />
                </Field>
              </div>
            </div>
            <Textarea
              value={notes}
              onChange={(ev) => onNotes(ev.target.value)}
              placeholder="Motivo o nota de la decisión (opcional)…"
              className="mb-3"
            />
            {/* PARCHE LOCAL (Raspberry Pi 5): alta desde la caseta.
                Con un vehiculo desconocido, registrarlo es la accion principal
                -es lo que de verdad resuelve el caso- y por eso va primero y
                destacada. 'Permitir' sin registrar deja pasar el coche pero la
                placa sigue sin existir manana, asi que queda como salida
                secundaria, no como la opcion obvia. */}
            {isUnknownVehicle && (
              <Button variant="primary" size="lg" icon={UserPlus} onClick={onRegister}
                className="mb-2 w-full">
                Registrar y dar entrada
              </Button>
            )}
            <div className="flex flex-wrap gap-2">
              <Button
                variant={isUnknownVehicle ? 'secondary' : 'success'}
                size="lg" icon={CheckCircle2} loading={deciding}
                onClick={() => onDecide('allow')} className="flex-1">
                {isUnknownVehicle ? 'Permitir sin registrar' : 'Permitir acceso'}
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

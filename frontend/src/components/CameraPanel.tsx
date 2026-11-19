/**
 * Vista en vivo de la cámara.
 *
 * PARCHE LOCAL (Raspberry Pi 5) — ver /opt/scae/PATCHES.md
 *
 * El vídeo llega como MJPEG desde `/api/v1/camera/stream`, que lo pinta un
 * `<img>` nativo: sin librerías, sin negociación y sin los segundos de latencia
 * que añade HLS. El navegador manda la cookie de sesión automáticamente por ser
 * mismo origen, así que el flujo queda protegido por la autenticación que ya
 * existe. **La URL RTSPS nunca llega al navegador.**
 *
 * La captura del servidor arranca con el primer espectador y se detiene sola,
 * así que este componente sólo monta el `<img>` cuando el usuario lo pide:
 * decodificar 4K cuesta ~93 % de un núcleo y la Pi comparte hardware con la
 * plataforma de monitoreo energético.
 */
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, Camera, DoorOpen, Pause, Play, RefreshCw, ScanLine, Shapes,
  VideoOff, Wifi,
} from 'lucide-react'

import { get, post } from '../lib/api'
import { Badge, Button, Card, CardHeader, Plate, Spinner, cx, decisionTone } from './ui'
import type { Tone } from './ui'
import { percent } from '../lib/format'

export type CameraStatus = {
  configured: boolean
  name: string
  state: 'not_configured' | 'stopped' | 'connecting' | 'live' | 'reconnecting' | 'error'
  detail: string
  viewers: number
  frames: number
  fps: number
  width: number | null
  height: number | null
  reconnects: number
  last_frame_at: number | null
}

const STATE_LABEL: Record<CameraStatus['state'], { text: string; tone: Tone }> = {
  not_configured: { text: 'Sin cámara configurada', tone: 'neutral' },
  stopped: { text: 'Detenida', tone: 'neutral' },
  connecting: { text: 'Conectando…', tone: 'review' },
  live: { text: 'En vivo', tone: 'allow' },
  reconnecting: { text: 'Reconectando…', tone: 'review' },
  error: { text: 'Error de captura', tone: 'deny' },
}

/** Estado del reconocimiento automatico sobre el video en vivo. */
export type RecognitionStatus = {
  enabled: boolean
  running: boolean
  state: 'stopped' | 'idle' | 'watching' | 'error'
  detail: string
  frames_seen: number
  frames_analysed: number
  plates_detected: number
  plates_read: number
  plates_unreadable: number
  plates_too_small: number
  last_guess: string | null
  last_consistency: number | null
  vehicles_seen: number
  events_created: number
  suppressed: number
  last_plate: string | null
  last_confidence: number | null
  last_decision: string | null
  last_event_id: number | null
  last_at: number | null
  avg_inference_ms: number
}

/** Geometria de lo ultimo detectado, en coordenadas del fotograma. */
export type OverlayBox = {
  x1: number; y1: number; x2: number; y2: number
  conf: number
  label?: string
  text?: string | null
  text_conf?: number | null
}

export type OverlayData = {
  at: number | null
  age_s: number | null
  fresh: boolean
  frame_width: number | null
  frame_height: number | null
  vehicles: OverlayBox[]
  plates: OverlayBox[]
  running: boolean
  inference_ms: number
}

/** Lo que devuelve una apertura manual. */
export type GateOpenResult = {
  event_id: number
  lane: string
  actuation_status: 'succeeded' | 'failed' | 'simulated' | 'not_required' | 'pending'
  actuation_detail: string | null
  occurred_at: string
}

const LPR_LABEL: Record<RecognitionStatus['state'], { text: string; tone: Tone }> = {
  stopped: { text: 'Detenido', tone: 'neutral' },
  idle: { text: 'Vigilando', tone: 'info' },
  watching: { text: 'Analizando', tone: 'review' },
  error: { text: 'Error', tone: 'deny' },
}

export function CameraPanel({ className }: { className?: string }) {
  // Arranca mirando. El carril se vigila 24/7 en el servidor, asi que la
  // captura ya esta abierta: un espectador mas solo cuesta entregar JPEG, no
  // decodificar otra vez. Antes empezaba en pausa porque abrir el flujo
  // implicaba arrancar FFmpeg, y eso ya no es asi.
  const [watching, setWatching] = useState(true)
  const [showOverlay, setShowOverlay] = useState(true)
  // Confirmacion en dos toques para la apertura manual: ver GateControl.
  const [gateArmed, setGateArmed] = useState(false)
  const [gateResult, setGateResult] = useState<GateOpenResult | null>(null)
  const [gateError, setGateError] = useState<string | null>(null)
  // Cambiar esta clave fuerza al navegador a reabrir el <img>, que es la única
  // forma de reintentar un MJPEG que se cortó.
  const [streamKey, setStreamKey] = useState(0)
  const [imgError, setImgError] = useState(false)
  const imgRef = useRef<HTMLImageElement>(null)

  const status = useQuery({
    queryKey: ['camera', 'status'],
    queryFn: () => get<CameraStatus>('/camera/status'),
    // Se sondea más a menudo mientras se mira, para que el estado no mienta.
    refetchInterval: watching ? 3_000 : 20_000,
  })

  const st = status.data
  const configured = st?.configured ?? false
  const label = STATE_LABEL[st?.state ?? 'stopped'] ?? STATE_LABEL.stopped

  const queryClient = useQueryClient()
  const lpr = useQuery({
    queryKey: ['camera', 'recognition'],
    queryFn: () => get<RecognitionStatus>('/camera/recognition'),
    // Mientras analiza interesa ver la ultima lectura casi al momento.
    refetchInterval: (q) => (q.state.data?.running ? 2_000 : 10_000),
  })
  const rec = lpr.data

  // Las cajas se piden aparte del video: el servidor manda coordenadas, no
  // pixeles dibujados, asi que esto no le cuesta CPU a la Pi. Ver la ruta
  // /camera/overlay en el backend.
  const overlay = useQuery({
    queryKey: ['camera', 'overlay'],
    queryFn: () => get<OverlayData>('/camera/overlay'),
    enabled: watching && showOverlay,
    refetchInterval: 500,
  })

  const openGate = useMutation({
    mutationFn: () => post<GateOpenResult>('/camera/gate/open', {}),
    onSuccess: (data) => {
      setGateError(null)
      setGateResult(data)
      setGateArmed(false)
      // La apertura deja un evento firmado, asi que las listas cambian.
      void queryClient.invalidateQueries({ queryKey: ['events'] })
    },
    onError: (e) => {
      setGateArmed(false)
      setGateError(e instanceof Error ? e.message : 'No se pudo abrir la pluma.')
    },
  })

  // El armado se desarma solo: un boton que se queda "a punto de abrir"
  // indefinidamente es una apertura accidental esperando a ocurrir.
  useEffect(() => {
    if (!gateArmed) return
    const t = setTimeout(() => setGateArmed(false), 4_000)
    return () => clearTimeout(t)
  }, [gateArmed])

  useEffect(() => {
    if (!gateResult) return
    const t = setTimeout(() => setGateResult(null), 8_000)
    return () => clearTimeout(t)
  }, [gateResult])

  const toggleLpr = useMutation({
    mutationFn: () =>
      post<unknown>(`/camera/recognition/${rec?.running ? 'stop' : 'start'}`, {}),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['camera', 'recognition'] })
      // Un evento automatico aparece en las listas de la consola.
      void queryClient.invalidateQueries({ queryKey: ['events'] })
    },
  })

  // Al desmontar, soltar el flujo: si no, el navegador mantiene la conexión
  // abierta y el servidor sigue decodificando para nadie.
  useEffect(() => {
    const img = imgRef.current
    return () => {
      if (img) img.src = ''
    }
  }, [])

  function start() {
    setImgError(false)
    setStreamKey((k) => k + 1)
    setWatching(true)
  }

  function stop() {
    if (imgRef.current) imgRef.current.src = ''
    setWatching(false)
  }

  return (
    <Card padded={false} className={cx('overflow-hidden', className)}>
      <div className="px-5 pt-5">
        <CardHeader
          title="Cámara del carril"
          subtitle={st?.name}
          icon={Camera}
          action={
            <div className="flex items-center gap-2">
              <Badge tone={label.tone} dot={st?.state === 'live'}>
                {label.text}
              </Badge>
              {watching && (
                <Button
                  variant="ghost"
                  icon={Shapes}
                  onClick={() => setShowOverlay((v) => !v)}
                  aria-pressed={showOverlay}
                  title={showOverlay ? 'Ocultar detecciones' : 'Mostrar detecciones'}
                >
                  <span className="hidden sm:inline">
                    {showOverlay ? 'Detecciones' : 'Sin cajas'}
                  </span>
                </Button>
              )}
              {watching ? (
                <Button variant="ghost" icon={Pause} onClick={stop}>
                  Pausar
                </Button>
              ) : (
                <Button
                  variant="primary"
                  icon={Play}
                  onClick={start}
                  disabled={!configured}
                >
                  Ver en vivo
                </Button>
              )}
            </div>
          }
        />
      </div>

      <div
        className="relative flex aspect-video w-full items-center justify-center border-t"
        style={{ background: 'var(--surface-sunken)', borderColor: 'var(--border)' }}
      >
        {!configured ? (
          <Placeholder
            icon={VideoOff}
            title="No hay cámara configurada"
            detail="Falta la URL del flujo en la configuración del servidor."
          />
        ) : !watching ? (
          <Placeholder
            icon={Camera}
            title="Vista en vivo en pausa"
            detail="La captura sólo consume recursos mientras alguien mira."
          />
        ) : imgError ? (
          <Placeholder
            icon={VideoOff}
            title="No se pudo mostrar el vídeo"
            detail={st?.detail || 'La cámara no está entregando imagen.'}
            action={
              <Button variant="ghost" icon={RefreshCw} onClick={start}>
                Reintentar
              </Button>
            }
          />
        ) : (
          <>
            <img
              ref={imgRef}
              key={streamKey}
              src={`/api/v1/camera/stream?t=${streamKey}`}
              alt="Vista en vivo de la cámara del carril"
              className="h-full w-full object-contain"
              onError={() => setImgError(true)}
            />
            {showOverlay && overlay.data?.fresh && (
              <DetectionOverlay data={overlay.data} />
            )}
            {st?.state !== 'live' && (
              <div className="absolute inset-0 grid place-items-center"
                   style={{ background: 'color-mix(in srgb, var(--surface-sunken) 80%, transparent)' }}>
                <Spinner label={label.text} />
              </div>
            )}
          </>
        )}
      </div>

      <GateControl
        armed={gateArmed}
        pending={openGate.isPending}
        result={gateResult}
        error={gateError}
        onArm={() => { setGateError(null); setGateArmed(true) }}
        onConfirm={() => openGate.mutate()}
        onCancel={() => setGateArmed(false)}
      />

      {configured && (
        <div className="border-t px-5 py-3" style={{ borderColor: 'var(--border)' }}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <ScanLine size={15} style={{ color: 'var(--text-muted)' }} />
              <span className="text-[13px] font-medium">Reconocimiento automatico</span>
              <Badge
                tone={(LPR_LABEL[rec?.state ?? 'stopped'] ?? LPR_LABEL.stopped).tone}
                dot={rec?.running}
              >
                {(LPR_LABEL[rec?.state ?? 'stopped'] ?? LPR_LABEL.stopped).text}
              </Badge>
            </div>
            <Button
              variant={rec?.running ? 'ghost' : 'secondary'}
              icon={rec?.running ? Pause : ScanLine}
              loading={toggleLpr.isPending}
              onClick={() => toggleLpr.mutate()}
            >
              {rec?.running ? 'Detener analisis' : 'Analizar placas'}
            </Button>
          </div>

          {rec?.last_plate && (
            <div
              className="mt-3 flex flex-wrap items-center gap-3 rounded-lg px-3 py-2"
              style={{ background: 'var(--surface-sunken)' }}
            >
              <span className="text-[12px]" style={{ color: 'var(--text-subtle)' }}>
                Ultima lectura
              </span>
              <Plate value={rec.last_plate} />
              {rec.last_confidence != null && (
                <span className="text-[12px]" style={{ color: 'var(--text-muted)' }}>
                  {percent(rec.last_confidence)} de confianza
                </span>
              )}
              {rec.last_decision && (
                <Badge tone={decisionTone(rec.last_decision)}>{rec.last_decision}</Badge>
              )}
              {/* Mejor conjetura cuando ninguna lectura llega a confirmarse: le
                  dice al guardia por donde empezar a teclear y cuánto fiarse. */}
              {rec.last_guess && rec.last_consistency != null && (
                <span className="w-full text-[12px]" style={{ color: 'var(--text-subtle)' }}>
                  Conjetura <span className="plate">{rec.last_guess}</span>
                  {rec.last_consistency < 1 || rec.plates_read > 1
                    ? ` · ${Math.round(rec.last_consistency * 100)}% de coincidencia`
                    : ''}
                  {' '}· sin confirmar
                </span>
              )}
            </div>
          )}

          {rec?.running && (
            <div
              className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px]"
              style={{ color: 'var(--text-subtle)' }}
            >
              <span>{rec.detail}</span>
              <span>{rec.frames_analysed} analizados de {rec.frames_seen}</span>
              <span>{rec.events_created} eventos</span>
              {rec.vehicles_seen > 0 && <span>{rec.vehicles_seen} con vehiculo</span>}
              {rec.plates_detected > 0 && (
                <span>{rec.plates_read} leidas de {rec.plates_detected} vistas</span>
              )}
              {rec.plates_unreadable > 0 && (
                <span>{rec.plates_unreadable} ilegibles</span>
              )}
              {rec.plates_too_small > 0 && (
                <span>{rec.plates_too_small} demasiado pequenas</span>
              )}
              {rec.suppressed > 0 && <span>{rec.suppressed} repetidas omitidas</span>}
              {rec.avg_inference_ms > 0 && <span>{rec.avg_inference_ms} ms/analisis</span>}
            </div>
          )}
        </div>
      )}

      {configured && (
        <div
          className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t px-5 py-2.5 text-[12px]"
          style={{ borderColor: 'var(--border)', color: 'var(--text-subtle)' }}
        >
          <span className="inline-flex items-center gap-1.5">
            <Wifi size={13} />
            {st?.detail || '—'}
          </span>
          {st?.state === 'live' && (
            <>
              <span>{st.fps.toFixed(1)} fps</span>
              {st.width && st.height && (
                <span>
                  {st.width}×{st.height}
                </span>
              )}
              <span>
                {st.viewers} {st.viewers === 1 ? 'espectador' : 'espectadores'}
              </span>
            </>
          )}
          {!!st?.reconnects && <span>{st.reconnects} reconexiones</span>}
        </div>
      )}
    </Card>
  )
}

/**
 * Apertura manual de la pluma.
 *
 * PARCHE LOCAL (Raspberry Pi 5) — ver /opt/scae/PATCHES.md
 *
 * Hace falta de verdad: un peatón, una ambulancia, un coche cuya placa la cámara
 * no ve, una avería. Hasta ahora la única forma de mover la pluma era resolver
 * un evento, y sin evento no había manera.
 *
 * **Dos toques, no uno.** Esto mueve hierro de verdad, y en una caseta la
 * pantalla se roza con el codo. El primer toque arma el botón, el segundo abre,
 * y si nadie confirma se desarma solo a los 4 segundos: un botón que se queda
 * «a punto de abrir» indefinidamente es una apertura accidental esperando a
 * ocurrir.
 *
 * Cada apertura deja un evento firmado por quien la pidió, no solo una línea de
 * auditoría: una barrera que se abre sin dejar registro es un agujero.
 */
function GateControl({
  armed, pending, result, error, onArm, onConfirm, onCancel,
}: {
  armed: boolean
  pending: boolean
  result: GateOpenResult | null
  error: string | null
  onArm: () => void
  onConfirm: () => void
  onCancel: () => void
}) {
  // `simulated` se muestra aparte a propósito: ningún informe debe confundir
  // una simulación con una pluma que de verdad se movió.
  const TONE: Record<string, { tone: Tone; text: string }> = {
    succeeded: { tone: 'allow', text: 'Pluma abierta' },
    simulated: { tone: 'review', text: 'Apertura simulada' },
    failed: { tone: 'deny', text: 'No se pudo abrir' },
    not_required: { tone: 'neutral', text: 'Sin actuación' },
    pending: { tone: 'review', text: 'En curso' },
  }

  return (
    <div className="border-t px-5 py-3" style={{ borderColor: 'var(--border)' }}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <DoorOpen size={15} style={{ color: 'var(--text-muted)' }} />
          <span className="text-[13px] font-medium">Barrera</span>
          <span className="text-[12px]" style={{ color: 'var(--text-subtle)' }}>
            {armed ? 'Pulsa de nuevo para confirmar' : 'Apertura manual'}
          </span>
        </div>
        {armed ? (
          <div className="flex items-center gap-2">
            <Button variant="ghost" onClick={onCancel}>Cancelar</Button>
            <Button variant="success" icon={DoorOpen} loading={pending}
              onClick={onConfirm}>
              Confirmar apertura
            </Button>
          </div>
        ) : (
          <Button variant="secondary" icon={DoorOpen} loading={pending} onClick={onArm}>
            Abrir pluma
          </Button>
        )}
      </div>

      {error && (
        <div className="mt-2.5 flex items-start gap-2 rounded-lg px-3 py-2 text-[12.5px]"
          style={{ background: 'var(--deny-bg)', color: 'var(--deny)' }}>
          <AlertTriangle size={14} className="mt-px shrink-0" />
          {error}
        </div>
      )}

      {result && (
        <div className="animate-in-up mt-2.5 flex flex-wrap items-center gap-2 rounded-lg px-3 py-2"
          style={{ background: 'var(--surface-sunken)' }}>
          <Badge tone={(TONE[result.actuation_status] ?? TONE.pending).tone}>
            {(TONE[result.actuation_status] ?? TONE.pending).text}
          </Badge>
          <span className="text-[12px]" style={{ color: 'var(--text-muted)' }}>
            Carril {result.lane} · evento {result.event_id}
          </span>
          {result.actuation_detail && (
            <span className="w-full text-[11.5px]" style={{ color: 'var(--text-subtle)' }}>
              {result.actuation_detail}
            </span>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * Rectangulos de deteccion sobre el video.
 *
 * Se dibuja como un SVG con `viewBox` del tamano del fotograma y el mismo
 * `preserveAspectRatio` que usa el `<img>` con `object-contain`. Asi el
 * navegador escala las cajas exactamente igual que la imagen, sin que haya que
 * medir el elemento ni recalcular nada al cambiar el tamano de la ventana: dos
 * fuentes de error que no hacen falta.
 *
 * Las cajas no vienen quemadas en el JPEG a proposito. Hacerlo obligaria al
 * servidor a decodificar, dibujar y recodificar cada fotograma en una Pi que
 * comparte CPU con la plataforma de la tesis, y ataria el overlay a los 24 fps
 * del video en vez de a los ~2 analisis por segundo que de verdad hay.
 */
function DetectionOverlay({ data }: { data: OverlayData }) {
  const w = data.frame_width ?? 1280
  const h = data.frame_height ?? 720
  // Trazo proporcional al fotograma, para que no se vea ni grueso ni invisible.
  const stroke = Math.max(2, Math.round(w / 420))
  const font = Math.max(12, Math.round(w / 58))

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="xMidYMid meet"
      className="pointer-events-none absolute inset-0 h-full w-full"
      aria-hidden="true"
    >
      {data.vehicles.map((b, i) => (
        <g key={`v${i}`}>
          <rect
            x={b.x1} y={b.y1} width={Math.max(0, b.x2 - b.x1)} height={Math.max(0, b.y2 - b.y1)}
            fill="none" stroke="var(--color-uc-400, #38bdf8)" strokeWidth={stroke}
            rx={stroke * 2}
          />
          <BoxLabel
            x={b.x1} y={b.y1} font={font} stroke={stroke}
            text={`${b.label ?? 'vehiculo'} ${Math.round(b.conf * 100)}%`}
            bg="var(--color-uc-400, #38bdf8)"
          />
        </g>
      ))}
      {data.plates.map((b, i) => (
        <g key={`p${i}`}>
          <rect
            x={b.x1} y={b.y1} width={Math.max(0, b.x2 - b.x1)} height={Math.max(0, b.y2 - b.y1)}
            fill="none" stroke="#facc15" strokeWidth={stroke} rx={stroke}
          />
          <BoxLabel
            x={b.x1} y={b.y2 + font * 1.25} font={font} stroke={stroke}
            text={
              b.text
                ? `${b.text}${b.text_conf != null ? ` ${Math.round(b.text_conf * 100)}%` : ''}`
                : `placa ${Math.round(b.conf * 100)}%`
            }
            bg="#facc15"
            dark
          />
        </g>
      ))}
    </svg>
  )
}

/** Etiqueta legible sobre cualquier fondo: pastilla de color con texto encima. */
function BoxLabel({
  x, y, text, font, stroke, bg, dark = false,
}: {
  x: number; y: number; text: string; font: number; stroke: number
  bg: string; dark?: boolean
}) {
  const padX = font * 0.35
  const width = text.length * font * 0.62 + padX * 2
  const top = Math.max(0, y - font * 1.35)
  return (
    <>
      <rect
        x={x} y={top} width={width} height={font * 1.3}
        fill={bg} rx={stroke}
      />
      <text
        x={x + padX} y={top + font * 0.98}
        fontSize={font} fontWeight={700}
        fill={dark ? '#1c1917' : '#ffffff'}
        style={{ fontFamily: 'ui-monospace, monospace' }}
      >
        {text}
      </text>
    </>
  )
}

function Placeholder({
  icon: Icon,
  title,
  detail,
  action,
}: {
  icon: typeof Camera
  title: string
  detail: string
  action?: React.ReactNode
}) {
  return (
    <div className="flex flex-col items-center px-6 py-10 text-center">
      <span
        className="mb-3 grid h-12 w-12 place-items-center rounded-2xl"
        style={{ background: 'var(--surface)', color: 'var(--text-subtle)' }}
      >
        <Icon size={20} />
      </span>
      <p className="text-[14px] font-medium">{title}</p>
      <p className="mt-1 max-w-sm text-[12.5px]" style={{ color: 'var(--text-muted)' }}>
        {detail}
      </p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

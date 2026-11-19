/**
 * Alta de un vehículo desconocido desde la caseta.
 *
 * PARCHE LOCAL (Raspberry Pi 5) — ver /opt/scae/PATCHES.md
 *
 * Cuando el reconocimiento lee una placa que no está en el padrón, el motor de
 * decisión la manda a revisión manual. Este diálogo es lo que el guardia hace a
 * continuación, y son las dos únicas cosas que de verdad pasan en una caseta:
 *
 *   · **Visitante** — entra hoy, por un motivo y un rato. Pase temporal.
 *   · **Credencial** — es de la universidad. Alta permanente con los datos
 *     impresos en la credencial.
 *
 * Las dos terminan permitiendo el paso y accionando la barrera, así que el
 * diálogo se cierra con el coche ya dentro: el guardia no tiene que acordarse
 * de pulsar «Permitir» después de registrar.
 *
 * La placa se puede corregir aquí mismo. Es deliberado: el momento en el que el
 * guardia está tecleando el nombre es justo cuando tiene la placa real delante,
 * y registrar a una persona con la placa que el OCR leyó mal dejaría el padrón
 * sucio para siempre.
 */
import { useEffect, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  AlertTriangle, BadgeCheck, ChevronLeft, GraduationCap, IdCard, RotateCcw,
  UserPlus,
} from 'lucide-react'

import { ApiError, post } from '../lib/api'
import type { AccessEventDetail } from '../lib/types'
import { Badge, Button, Field, Input, Modal, Plate, Select, Textarea } from './ui'

type Mode = 'choose' | 'visitor' | 'credential'

export type IntakeResponse = {
  event: AccessEventDetail
  registration: {
    kind: 'visitor' | 'credential'
    person_name: string
    plate_display: string
    permit_code: string
    valid_until: string | null
    institutional_id?: string
    person_type?: string
  }
}

/** Duraciones que un guardia elige de verdad, en vez de un campo libre. */
const DURATIONS = [
  { value: '1', label: '1 hora' },
  { value: '2', label: '2 horas' },
  { value: '4', label: '4 horas (media jornada)' },
  { value: '8', label: '8 horas (jornada)' },
  { value: '12', label: '12 horas' },
  { value: '24', label: '24 horas (máximo)' },
]

/** Un vehículo del padrón con placa parecida a la leída. */
export type SimilarVehicle = {
  id: number
  plate: string
  plate_display: string
  distance: number
  owner_name: string | null
  owner_type: string | null
  brand: string | null
  color: string | null
}

export function IntakeDialog({
  event,
  open,
  onClose,
  onDone,
  similar = [],
  onUseExisting,
}: {
  event: AccessEventDetail | null
  open: boolean
  onClose: () => void
  onDone: (result: IntakeResponse) => void
  /** Placas del padrón parecidas a la leída. */
  similar?: SimilarVehicle[]
  /** Dar paso con una placa ya registrada, sin crear nada nuevo. */
  onUseExisting?: (plate: string) => void
}) {
  const [mode, setMode] = useState<Mode>('choose')
  const [error, setError] = useState<string | null>(null)

  // Campos comunes
  const [plate, setPlate] = useState('')
  const [fullName, setFullName] = useState('')
  const [brand, setBrand] = useState('')
  const [color, setColor] = useState('')
  // Visitante
  const [reason, setReason] = useState('')
  const [hours, setHours] = useState('4')
  // Credencial
  const [account, setAccount] = useState('')
  const [personType, setPersonType] = useState<'student' | 'staff'>('student')

  // Al abrir con otro evento, empezar de cero y precargar la placa leída.
  useEffect(() => {
    if (!open) return
    setMode('choose')
    setError(null)
    setPlate(event?.plate_display ?? '')
    setFullName(''); setReason(''); setHours('4')
    setAccount(''); setPersonType('student')
    setBrand(''); setColor('')
  }, [open, event?.id, event?.plate_display])

  const submit = useMutation({
    mutationFn: (payload: { path: string; body: Record<string, unknown> }) =>
      post<IntakeResponse>(`/events/${event!.id}/${payload.path}`, payload.body),
    onSuccess: (data) => { setError(null); onDone(data) },
    onError: (e) =>
      setError(e instanceof ApiError ? e.message : 'No se pudo completar el registro.'),
  })

  if (!event) return null

  const plateChanged =
    plate.trim().toUpperCase() !== (event.plate_display ?? '').toUpperCase()
  const platePayload = plate.trim() ? { plate: plate.trim() } : {}
  const vehiclePayload = {
    ...(brand.trim() ? { brand: brand.trim() } : {}),
    ...(color.trim() ? { color: color.trim() } : {}),
  }

  function submitVisitor() {
    submit.mutate({
      path: 'register-visitor',
      body: {
        full_name: fullName.trim(),
        visit_reason: reason.trim(),
        hours: Number(hours),
        ...platePayload,
        ...vehiclePayload,
      },
    })
  }

  function submitCredential() {
    submit.mutate({
      path: 'register-credential',
      body: {
        full_name: fullName.trim(),
        institutional_id: account.trim(),
        person_type: personType,
        ...platePayload,
        ...vehiclePayload,
      },
    })
  }

  // Sin placa no hay nada que registrar: el alta crea un VEHICULO, y uno sin
  // placa no se puede volver a reconocer manana.
  const plateReady = plate.trim().length >= 4
  const visitorReady =
    plateReady && fullName.trim().length >= 3 && reason.trim().length >= 3
  const credentialReady =
    plateReady && fullName.trim().length >= 3 && account.trim().length >= 3

  const titles: Record<Mode, string> = {
    choose: 'Vehículo no registrado',
    visitor: 'Registrar visitante',
    credential: 'Registrar placa permanentemente',
  }
  const descriptions: Record<Mode, string> = {
    choose: 'Esta placa no está en el padrón. Elige cómo darle entrada.',
    visitor: 'Pase temporal para esta puerta. Caduca solo.',
    credential: 'Datos tal como aparecen en la credencial universitaria.',
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={titles[mode]}
      description={descriptions[mode]}
      width={mode === 'choose' ? 'md' : 'lg'}
      footer={
        mode === 'choose' ? (
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
        ) : (
          <>
            <Button variant="ghost" icon={ChevronLeft} onClick={() => setMode('choose')}>
              Atrás
            </Button>
            <Button
              variant="success"
              icon={BadgeCheck}
              loading={submit.isPending}
              disabled={mode === 'visitor' ? !visitorReady : !credentialReady}
              onClick={mode === 'visitor' ? submitVisitor : submitCredential}
            >
              {mode === 'visitor' ? 'Dar entrada y abrir' : 'Registrar y abrir'}
            </Button>
          </>
        )
      }
    >
      {/* La placa, EDITABLE y lo primero que se ve.
          Va arriba y no enterrada al final del formulario porque corregirla es
          lo primero que hace el guardia cuando el OCR se equivoca, y registrar
          a una persona con la placa mal leída ensucia el padrón para siempre. */}
      <div
        className="mb-4 rounded-lg px-3 py-3"
        style={{ background: 'var(--surface-sunken)' }}
      >
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-[190px] flex-1">
            <Field
              label="Placa"
              required
              hint={
                plateChanged
                  ? 'Corregida respecto a lo que leyó la cámara.'
                  : 'Compruébala contra el vehículo y corrígela si hace falta.'
              }
            >
              <Input
                value={plate}
                onChange={(e) => setPlate(e.target.value.toUpperCase())}
                placeholder="Teclea la placa"
                className="plate uppercase text-[17px]"
                maxLength={20}
              />
            </Field>
          </div>
          <div className="flex flex-col gap-1.5 pb-1">
            <span className="text-[11px]" style={{ color: 'var(--text-subtle)' }}>
              La cámara leyó
            </span>
            <div className="flex items-center gap-2">
              <span
                className={event.plate_display ? 'plate text-[14px] font-semibold' : 'text-[13px]'}
                style={event.plate_display ? undefined : { color: 'var(--text-subtle)' }}
              >
                {event.plate_display ?? 'sin lectura — tecléala tú'}
              </span>
              {event.plate_confidence != null && (
                <Badge tone={event.plate_confidence >= 0.8 ? 'allow' : 'review'}>
                  {Math.round(event.plate_confidence * 100)}%
                </Badge>
              )}
            </div>
          </div>
          {plateChanged && event.plate_display && (
            <Button
              size="sm"
              variant="ghost"
              icon={RotateCcw}
              onClick={() => setPlate(event.plate_display ?? '')}
            >
              Deshacer
            </Button>
          )}
        </div>
        {/* La conjetura del reconocimiento cuando no pudo confirmar nada: le da
            al guardia por dónde empezar en vez de dejarle el campo vacío. */}
        {!event.plate_display &&
          (event.notes?.includes('onjetura') || event.notes?.includes('ectura')) && (
          <p className="mt-2 text-[12px]" style={{ color: 'var(--text-muted)' }}>
            {event.notes}
          </p>
        )}
      </div>

      {error && (
        <div
          role="alert"
          className="mb-4 flex items-start gap-2 rounded-lg px-3 py-2.5 text-[13px]"
          style={{ background: 'var(--deny-bg)', color: 'var(--deny)' }}
        >
          <AlertTriangle size={16} className="mt-px shrink-0" />
          {error}
        </div>
      )}

      {/* Ya registrado, con la placa leída de otra forma.
          Es el caso que llenaba el padrón de duplicados: con una placa de poco
          contraste el OCR lee algo distinto cada vez —FTC224A, FTC324A,
          VTC324A, FTG224A…— del mismo coche, y el guardia acababa dando de alta
          a la misma persona una y otra vez. Un parecido NO identifica, así que
          decide él; pero al menos lo ve. */}
      {mode === 'choose' && similar.length > 0 && onUseExisting && (
        <div
          className="mb-4 rounded-xl border px-4 py-3"
          style={{ borderColor: 'var(--review)', background: 'var(--review-bg)' }}
        >
          <p className="flex items-center gap-2 text-[13px] font-semibold"
            style={{ color: 'var(--review)' }}>
            <AlertTriangle size={15} />
            ¿Es alguno de estos, ya registrados?
          </p>
          <p className="mt-1 text-[12px]" style={{ color: 'var(--review)', opacity: 0.85 }}>
            La placa leída se parece mucho. Si es el mismo coche, dale paso con su
            registro en vez de darlo de alta otra vez.
          </p>
          <ul className="mt-2.5 space-y-1.5">
            {similar.map((v) => (
              <li key={v.id}>
                <button
                  onClick={() => onUseExisting(v.plate_display)}
                  className="flex w-full flex-wrap items-center gap-2.5 rounded-lg border px-3 py-2 text-left transition-colors hover:bg-[var(--surface-sunken)]"
                  style={{ borderColor: 'var(--border)', background: 'var(--surface-raised)' }}
                >
                  <Plate value={v.plate_display} size="sm" />
                  <span className="text-[13px] font-medium">
                    {v.owner_name ?? 'sin titular'}
                  </span>
                  <span className="text-[12px]" style={{ color: 'var(--text-muted)' }}>
                    {[v.brand, v.color].filter(Boolean).join(' · ')}
                  </span>
                  <Badge tone="neutral" className="ml-auto">
                    {v.distance === 1 ? '1 carácter distinto' : `${v.distance} caracteres distintos`}
                  </Badge>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {mode === 'choose' && (
        <div className="grid gap-3 sm:grid-cols-2">
          <ChoiceCard
            icon={UserPlus}
            title="Visitante"
            detail="Entra hoy por un motivo concreto. Se le crea un pase temporal que caduca solo."
            onClick={() => setMode('visitor')}
          />
          <ChoiceCard
            icon={IdCard}
            title="Registrar placa"
            detail="Es de la universidad. Alta permanente en el padrón con los datos de su credencial."
            onClick={() => setMode('credential')}
          />
        </div>
      )}

      {mode !== 'choose' && (
        <div className="space-y-4">
          <Field label="Nombre completo" required
            hint="Como aparece en su identificación.">
            <Input
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Nombre(s) y apellidos"
              autoFocus
              maxLength={160}
            />
          </Field>

          {mode === 'visitor' ? (
            <>
              <Field label="Motivo de entrada" required>
                <Textarea
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Proveedor, trámite, visita a una facultad…"
                  maxLength={500}
                />
              </Field>
              <Field label="Tiempo dentro de las instalaciones" required
                hint="El pase caduca solo al cumplirse. Para una estancia más larga, registra la placa.">
                <Select value={hours} onChange={(e) => setHours(e.target.value)}>
                  {DURATIONS.map((d) => (
                    <option key={d.value} value={d.value}>{d.label}</option>
                  ))}
                </Select>
              </Field>
            </>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Número de cuenta" required
                hint="El que viene impreso en la credencial.">
                <Input
                  value={account}
                  onChange={(e) => setAccount(e.target.value.toUpperCase())}
                  placeholder="20184567"
                  className="num uppercase"
                  maxLength={32}
                />
              </Field>
              <Field label="Tipo" required>
                <Select
                  value={personType}
                  onChange={(e) => setPersonType(e.target.value as 'student' | 'staff')}
                >
                  <option value="student">Estudiante</option>
                  <option value="staff">Trabajador</option>
                </Select>
              </Field>
            </div>
          )}

          {/* Corrección de placa y datos del vehículo */}
          <div
            className="rounded-lg border px-3 py-3"
            style={{ borderColor: 'var(--border)' }}
          >
            <p className="mb-2.5 text-[11px] font-semibold uppercase tracking-wider"
              style={{ color: 'var(--text-subtle)' }}>
              Datos del vehículo (opcional)
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Marca y modelo">
                <Input value={brand} onChange={(e) => setBrand(e.target.value)}
                  placeholder="Nissan Versa" maxLength={60} />
              </Field>
              <Field label="Color">
                <Input value={color} onChange={(e) => setColor(e.target.value)}
                  placeholder="Blanco" maxLength={40} />
              </Field>
            </div>
          </div>

          <p className="text-[12px]" style={{ color: 'var(--text-subtle)' }}>
            {mode === 'visitor'
              ? 'Se registrará la entrada a tu nombre y se abrirá la barrera.'
              : 'La placa quedará en el padrón y las siguientes entradas serán automáticas.'}
          </p>
        </div>
      )}
    </Modal>
  )
}

function ChoiceCard({
  icon: Icon, title, detail, onClick,
}: {
  icon: typeof UserPlus
  title: string
  detail: string
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className="flex flex-col items-start rounded-xl border px-4 py-4 text-left transition-colors hover:bg-[var(--surface-sunken)]"
      style={{ borderColor: 'var(--border)' }}
    >
      <span className="mb-2.5 grid h-10 w-10 place-items-center rounded-lg"
        style={{ background: 'var(--surface-sunken)', color: 'var(--color-uc-600)' }}>
        <Icon size={19} />
      </span>
      <p className="text-[14.5px] font-semibold">{title}</p>
      <p className="mt-1 text-[12.5px]" style={{ color: 'var(--text-muted)' }}>
        {detail}
      </p>
    </button>
  )
}

/** Resumen de lo que se acaba de registrar, para la consola. */
export function IntakeSummary({ result }: { result: IntakeResponse }) {
  const r = result.registration
  const isVisitor = r.kind === 'visitor'
  return (
    <div
      className="animate-in-up flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg px-4 py-3 text-[13px]"
      style={{ background: 'var(--allow-bg)', color: 'var(--allow)' }}
    >
      {isVisitor ? <UserPlus size={16} /> : <GraduationCap size={16} />}
      <span className="font-semibold">{r.person_name}</span>
      <span className="plate">{r.plate_display}</span>
      <Badge tone="allow">{isVisitor ? 'Pase' : 'Permiso'} {r.permit_code}</Badge>
      <span style={{ opacity: 0.85 }}>
        {r.valid_until
          ? `vence ${new Date(r.valid_until).toLocaleString('es-MX', {
              hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short',
            })}`
          : 'registro permanente'}
      </span>
    </div>
  )
}

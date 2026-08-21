import { format, formatDistanceToNowStrict, parseISO } from 'date-fns'
import { es } from 'date-fns/locale'

/** The API serialises UTC; SQLite rows arrive without a zone marker. */
function toDate(value: string): Date {
  const hasZone = /[zZ]|[+-]\d{2}:?\d{2}$/.test(value)
  return parseISO(hasZone ? value : `${value}Z`)
}

export function dateTime(value?: string | null): string {
  if (!value) return '—'
  return format(toDate(value), "d MMM yyyy · HH:mm", { locale: es })
}

export function timeOnly(value?: string | null): string {
  if (!value) return '—'
  return format(toDate(value), 'HH:mm:ss', { locale: es })
}

export function dateOnly(value?: string | null): string {
  if (!value) return '—'
  return format(toDate(value), "d 'de' MMMM yyyy", { locale: es })
}

export function relative(value?: string | null): string {
  if (!value) return '—'
  return `hace ${formatDistanceToNowStrict(toDate(value), { locale: es })}`
}

export function percent(value?: number | null): string {
  if (value === null || value === undefined) return '—'
  return `${Math.round(value * 100)}%`
}

export function count(value: number): string {
  return value.toLocaleString('es-MX')
}

export const PERSON_TYPES: Record<string, string> = {
  student: 'Estudiante',
  academic: 'Académico',
  staff: 'Administrativo',
  visitor: 'Visitante',
  supplier: 'Proveedor',
}

export const ROLES: Record<string, string> = {
  admin: 'Administrador',
  supervisor: 'Supervisor',
  guard: 'Guardia',
  auditor: 'Auditor',
}

export const AUTH_STATUS: Record<string, string> = {
  active: 'Vigente',
  suspended: 'Suspendido',
  revoked: 'Revocado',
  expired: 'Expirado',
}

export const INCIDENT_STATUS: Record<string, string> = {
  open: 'Abierta',
  in_review: 'En revisión',
  resolved: 'Resuelta',
  dismissed: 'Descartada',
}

export const INCIDENT_TYPES: Record<string, string> = {
  infraction: 'Infracción',
  watchlist_hit: 'Lista de control',
  unauthorized_attempt: 'Intento no autorizado',
  tailgating: 'Acceso en cadena',
  device_failure: 'Falla de dispositivo',
  other: 'Otro',
}

export const SEVERITY: Record<string, string> = {
  low: 'Baja',
  medium: 'Media',
  high: 'Alta',
  critical: 'Crítica',
}

export const LANE_DIRECTION: Record<string, string> = {
  entry: 'Entrada',
  exit: 'Salida',
  bidirectional: 'Bidireccional',
}

export const EVENT_SOURCE: Record<string, string> = {
  camera: 'Cámara',
  manual_upload: 'Carga manual',
  manual_entry: 'Captura manual',
  simulation: 'Simulación',
}

export const ACTUATION: Record<string, string> = {
  not_required: 'Sin apertura',
  pending: 'Pendiente',
  succeeded: 'Barrera abierta',
  failed: 'Fallo de apertura',
  simulated: 'Apertura simulada',
}

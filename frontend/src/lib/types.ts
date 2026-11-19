export type Role = 'admin' | 'supervisor' | 'guard' | 'auditor'
export type Decision = 'allow' | 'deny' | 'manual_review'
export type DataOrigin = 'real' | 'demo'

export interface LaneRef {
  id: number
  code: string
  name: string
  access_point_name?: string | null
  campus_name?: string | null
}

export interface CurrentUser {
  id: number
  username: string
  full_name: string
  email?: string | null
  role: Role
  must_change_password: boolean
  default_lane?: LaneRef | null
  last_login_at?: string | null
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface AccessEvent {
  id: number
  occurred_at: string
  source: string
  lane_id?: number | null
  lane_name?: string | null
  access_point_name?: string | null
  campus_name?: string | null
  plate_read?: string | null
  plate_normalized?: string | null
  plate_display?: string | null
  plate_confidence?: number | null
  vehicle_id?: number | null
  person_id?: number | null
  person_name?: string | null
  person_type?: string | null
  vehicle_label?: string | null
  authorization_id?: number | null
  decision: Decision
  decision_reason: string
  decision_label: string
  reason_label: string
  is_manual: boolean
  decided_by_name?: string | null
  resolved_at?: string | null
  notes?: string | null
  actuation_status: string
  actuation_detail?: string | null
  has_evidence: boolean
  processing_ms?: number | null
  origin: DataOrigin
}

export interface Detection {
  id: number
  detector: string
  object_class?: string | null
  object_confidence?: number | null
  ocr_engine?: string | null
  ocr_candidates: PlateCandidate[]
}

export interface PlateCandidate {
  plate: string
  display: string
  layout: string
  repaired: boolean
  edits?: number
  score: number
}

export interface AccessEventDetail extends AccessEvent {
  detections: Detection[]
  plate_candidates: PlateCandidate[]
}

export interface SimilarVehicle {
  id: number
  plate: string
  plate_display: string
  distance: number
  owner_name: string | null
  owner_type: string | null
  brand: string | null
  color: string | null
}

export interface CaptureResponse {
  event: AccessEventDetail
  matched_person?: {
    id: number
    full_name: string
    institutional_id?: string | null
    person_type: string
    faculty?: string | null
    photo_url?: string | null
    email?: string | null
  } | null
  matched_vehicle?: {
    id: number
    plate_display: string
    brand?: string | null
    model?: string | null
    color?: string | null
    year?: number | null
  } | null
  active_authorization?: {
    id: number
    status: string
    permit_code?: string | null
    valid_until?: string | null
  } | null
  recognition_backend: string
  /** Placas del padrón parecidas, cuando no hubo coincidencia exacta. */
  similar_vehicles?: SimilarVehicle[]
}

export interface TrendPoint {
  bucket: string
  allow: number
  deny: number
  manual_review: number
}

export interface DashboardStats {
  range_hours: number
  total_events: number
  allowed: number
  denied: number
  manual_review: number
  pending_review: number
  open_incidents: number
  unknown_vehicles: number
  active_shifts: number
  registered_vehicles: number
  active_authorizations: number
  watchlist_active: number
  avg_processing_ms?: number | null
  trend: TrendPoint[]
  reasons: { key: string; label: string; count: number }[]
  busiest_lanes: { lane_id: number; lane: string; access_point?: string | null; count: number }[]
  demo_data_present: boolean
}

export interface SystemComponent {
  key: string
  label: string
  status: 'operational' | 'simulated' | 'not_implemented' | 'degraded'
  detail: string
}

export interface SystemHealth {
  environment: string
  database: string
  recognition_backend: string
  recognition_ready: boolean
  components: SystemComponent[]
  demo_data_present: boolean
  version: string
}

export interface Vehicle {
  id: number
  plate: string
  plate_display: string
  state_code?: string | null
  brand?: string | null
  model?: string | null
  color?: string | null
  year?: number | null
  owner_id?: number | null
  owner_name?: string | null
  is_active: boolean
  origin: DataOrigin
  active_authorizations: number
  is_watchlisted: boolean
}

export interface Person {
  id: number
  institutional_id?: string | null
  first_name: string
  last_name: string
  full_name: string
  person_type: string
  email?: string | null
  phone?: string | null
  faculty?: string | null
  is_active: boolean
  origin: DataOrigin
  vehicle_count: number
}

export interface Authorization {
  id: number
  vehicle_id: number
  plate_display?: string | null
  person_id?: number | null
  person_name?: string | null
  campus_name?: string | null
  access_point_name?: string | null
  status: string
  valid_from: string
  valid_until?: string | null
  permit_code?: string | null
  reason?: string | null
  origin: DataOrigin
  scope_label: string
  is_currently_valid: boolean
}

export interface Incident {
  id: number
  incident_type: string
  severity: string
  status: string
  title: string
  description?: string | null
  event_id?: number | null
  plate_display?: string | null
  person_name?: string | null
  lane_name?: string | null
  reported_by_name?: string | null
  resolved_by_name?: string | null
  resolved_at?: string | null
  resolution_notes?: string | null
  notified_at?: string | null
  notification_error?: string | null
  created_at: string
  origin: DataOrigin
}

export interface WatchlistEntry {
  id: number
  plate: string
  plate_display: string
  severity: string
  reason: string
  is_active: boolean
  origin: DataOrigin
  created_at: string
}

export interface Campus {
  id: number
  code: string
  name: string
  city?: string | null
  is_active: boolean
  origin: DataOrigin
  access_point_count: number
}

export interface AccessPoint {
  id: number
  campus_id: number
  campus_name?: string | null
  code: string
  name: string
  description?: string | null
  is_active: boolean
  origin: DataOrigin
  lane_count: number
}

export interface Lane {
  id: number
  access_point_id: number
  access_point_name?: string | null
  campus_name?: string | null
  code: string
  name: string
  direction: string
  is_active: boolean
  auto_open_on_allow: boolean
  origin: DataOrigin
  camera_count: number
  controller_count: number
}

export interface Device {
  id: number
  lane_id: number
  lane_name?: string | null
  access_point_name?: string | null
  code: string
  name: string
  driver: string
  config: Record<string, unknown>
  status: string
  is_active: boolean
  origin: DataOrigin
  driver_implemented: boolean
}

export interface DriverCatalog {
  cameras: { kind: string; implemented: boolean; requires_config: string[] }[]
  barriers: { kind: string; implemented: boolean; requires_config: string[] }[]
}

export interface AppUser {
  id: number
  username: string
  full_name: string
  email?: string | null
  role: Role
  is_active: boolean
  default_lane_id?: number | null
  default_lane_name?: string | null
  last_login_at?: string | null
  must_change_password: boolean
  origin: DataOrigin
}

export interface AuditEntry {
  id: number
  at: string
  actor_id?: number | null
  actor_label: string
  action: string
  entity_type?: string | null
  entity_id?: string | null
  summary?: string | null
  ip_address?: string | null
}

export interface Shift {
  id: number
  user_id: number
  user_name?: string | null
  lane_name?: string | null
  started_at: string
  ended_at?: string | null
  is_open: boolean
  duration_minutes?: number | null
}

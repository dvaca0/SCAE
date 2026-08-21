/**
 * API client.
 *
 * The session lives in an httpOnly cookie, so nothing here stores a token.
 * Mutations echo the CSRF cookie back in a header, which is what proves the
 * request came from our own page rather than a third-party site.
 */

const BASE = '/api/v1'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(^|;\\s*)${name}=([^;]*)`))
  return match ? decodeURIComponent(match[2]) : null
}

type Options = {
  method?: string
  body?: unknown
  formData?: FormData
  signal?: AbortSignal
}

export async function api<T>(path: string, options: Options = {}): Promise<T> {
  const { method = 'GET', body, formData, signal } = options
  const headers: Record<string, string> = {}

  if (method !== 'GET') {
    const csrf = readCookie('scae_csrf')
    if (csrf) headers['X-CSRF-Token'] = csrf
  }
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  const response = await fetch(`${BASE}${path}`, {
    method,
    headers,
    credentials: 'same-origin',
    body: formData ?? (body !== undefined ? JSON.stringify(body) : undefined),
    signal,
  })

  if (response.status === 204) return undefined as T

  const text = await response.text()
  const payload = text ? safeJson(text) : null

  if (!response.ok) {
    const detail =
      (payload && typeof payload === 'object' && 'detail' in payload
        ? String((payload as { detail: unknown }).detail)
        : null) ?? `Error ${response.status}`
    throw new ApiError(response.status, detail)
  }
  return payload as T
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

export const get = <T,>(path: string, signal?: AbortSignal) => api<T>(path, { signal })
export const post = <T,>(path: string, body?: unknown) => api<T>(path, { method: 'POST', body })
export const put = <T,>(path: string, body?: unknown) => api<T>(path, { method: 'PUT', body })
export const patch = <T,>(path: string, body?: unknown) => api<T>(path, { method: 'PATCH', body })
export const del = <T,>(path: string) => api<T>(path, { method: 'DELETE' })
export const upload = <T,>(path: string, formData: FormData) =>
  api<T>(path, { method: 'POST', formData })

/** Query-string builder that drops empty values. */
export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    search.set(key, String(value))
  }
  const out = search.toString()
  return out ? `?${out}` : ''
}

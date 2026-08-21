import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { ApiError, get, post } from './api'
import type { CurrentUser } from './types'

interface AuthState {
  user: CurrentUser | null
  loading: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refresh: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      setUser(await get<CurrentUser>('/auth/me'))
    } catch (error) {
      // A 401 here is the normal "not signed in" case, not a failure.
      if (!(error instanceof ApiError && error.status === 401)) console.error(error)
      setUser(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const login = useCallback(async (username: string, password: string) => {
    const result = await post<{ user: CurrentUser }>('/auth/login', { username, password })
    setUser(result.user)
  }, [])

  const logout = useCallback(async () => {
    try {
      await post('/auth/logout')
    } finally {
      setUser(null)
    }
  }, [])

  const value = useMemo(
    () => ({ user, loading, login, logout, refresh }),
    [user, loading, login, logout, refresh],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth debe usarse dentro de AuthProvider')
  return context
}

/** Capability checks, kept in one place so the UI and API agree on roles. */
export const can = {
  manageUsers: (r?: string) => r === 'admin',
  manageInfrastructure: (r?: string) => r === 'admin',
  manageRegistry: (r?: string) => r === 'admin' || r === 'supervisor',
  operateLane: (r?: string) => r === 'admin' || r === 'supervisor' || r === 'guard',
  viewAudit: (r?: string) => r === 'admin' || r === 'supervisor' || r === 'auditor',
}

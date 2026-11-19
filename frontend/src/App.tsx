import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from './lib/auth'
import { AdminLayout } from './components/AdminLayout'
import { Spinner } from './components/ui'
import { Login } from './pages/Login'
import { Dashboard } from './pages/Dashboard'
import { Events } from './pages/Events'
import { Incidents } from './pages/Incidents'
import { GuardConsole } from './pages/GuardConsole'
import { Vehicles } from './pages/Vehicles'
import { People } from './pages/People'
import { Authorizations } from './pages/Authorizations'
import { Infrastructure } from './pages/Infrastructure'
import { SystemStatus } from './pages/SystemStatus'
import { Users } from './pages/Users'
import { Audit } from './pages/Audit'
import { NotFound } from './pages/NotFound'

export function App() {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="grid min-h-screen place-items-center">
        <Spinner label="Cargando SCAE…" />
      </div>
    )
  }

  if (!user) return <Login />

  // A guard signs in to the lane console, not to the administrative shell.
  // Same platform, deliberately different product for a different job.
  if (user.role === 'guard') {
    return (
      <Routes>
        <Route path="/consola" element={<GuardConsole />} />
        <Route path="*" element={<Navigate to="/consola" replace />} />
      </Routes>
    )
  }

  return (
    <AdminLayout>
      <Routes>
        <Route path="/" element={<Navigate to="/panel" replace />} />
        <Route path="/panel" element={<Dashboard />} />
        <Route path="/eventos" element={<Events />} />
        <Route path="/incidencias" element={<Incidents />} />
        <Route path="/consola" element={<GuardConsole embedded />} />
        <Route path="/vehiculos" element={<Vehicles />} />
        <Route path="/personas" element={<People />} />
        <Route path="/permisos" element={<Authorizations />} />
        <Route path="/infraestructura" element={<Infrastructure />} />
        <Route path="/sistema" element={<SystemStatus />} />
        <Route path="/usuarios" element={<Users />} />
        <Route path="/auditoria" element={<Audit />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </AdminLayout>
  )
}

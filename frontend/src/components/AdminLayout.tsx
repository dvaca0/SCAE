import { useState, type ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import {
  Activity, ClipboardList, Car, Users, ShieldAlert, KeyRound, Building2,
  ScrollText, UserCog, Moon, Sun, LogOut, Menu, X, Radio, HeartPulse,
} from 'lucide-react'
import { Wordmark } from './Brand'
import { Badge, Button, cx } from './ui'
import { can, useAuth } from '../lib/auth'
import { useTheme } from '../lib/theme'
import { ROLES } from '../lib/format'

interface NavItem {
  to: string
  label: string
  icon: typeof Activity
  visible: (role?: string) => boolean
}

const SECTIONS: { heading: string; items: NavItem[] }[] = [
  {
    heading: 'Operación',
    items: [
      { to: '/panel', label: 'Panel general', icon: Activity, visible: () => true },
      { to: '/eventos', label: 'Eventos de acceso', icon: ClipboardList, visible: () => true },
      { to: '/incidencias', label: 'Incidencias', icon: ShieldAlert, visible: () => true },
      { to: '/consola', label: 'Consola de caseta', icon: Radio, visible: can.operateLane },
    ],
  },
  {
    heading: 'Padrón',
    items: [
      { to: '/vehiculos', label: 'Vehículos', icon: Car, visible: () => true },
      { to: '/personas', label: 'Personas', icon: Users, visible: () => true },
      { to: '/permisos', label: 'Permisos', icon: KeyRound, visible: () => true },
    ],
  },
  {
    heading: 'Plataforma',
    items: [
      { to: '/infraestructura', label: 'Infraestructura', icon: Building2, visible: () => true },
      { to: '/sistema', label: 'Estado del sistema', icon: HeartPulse, visible: () => true },
      { to: '/usuarios', label: 'Usuarios', icon: UserCog, visible: can.manageUsers },
      { to: '/auditoria', label: 'Auditoría', icon: ScrollText, visible: can.viewAudit },
    ],
  },
]

export function AdminLayout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth()
  const { theme, toggle } = useTheme()
  const [mobileOpen, setMobileOpen] = useState(false)
  const location = useLocation()

  const sidebar = (
    <nav className="flex h-full flex-col">
      <div className="px-5 py-5">
        <Wordmark />
      </div>

      <div className="flex-1 overflow-y-auto px-3 pb-4">
        {SECTIONS.map((section) => {
          const items = section.items.filter((i) => i.visible(user?.role))
          if (!items.length) return null
          return (
            <div key={section.heading} className="mb-5">
              <p
                className="px-2.5 pb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.11em]"
                style={{ color: 'var(--text-subtle)' }}
              >
                {section.heading}
              </p>
              <ul className="space-y-0.5">
                {items.map(({ to, label, icon: Icon }) => (
                  <li key={to}>
                    <NavLink
                      to={to}
                      onClick={() => setMobileOpen(false)}
                      className={({ isActive }) =>
                        cx(
                          'flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13.5px] font-medium transition-colors',
                          isActive
                            ? 'bg-uc-600 text-white shadow-sm'
                            : 'hover:bg-[var(--surface-sunken)]',
                        )
                      }
                    >
                      <Icon size={16} strokeWidth={2} />
                      {label}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </div>
          )
        })}
      </div>

      <div className="border-t px-3 py-3" style={{ borderColor: 'var(--border)' }}>
        <div className="flex items-center gap-2.5 rounded-lg px-2.5 py-2">
          <span
            className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-[12px] font-bold text-white"
            style={{ background: 'var(--color-uc-600)' }}
          >
            {user?.full_name.slice(0, 1).toUpperCase()}
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[13px] font-medium">{user?.full_name}</p>
            <p className="truncate text-[11.5px]" style={{ color: 'var(--text-subtle)' }}>
              {ROLES[user?.role ?? ''] ?? user?.role}
            </p>
          </div>
        </div>
        <div className="mt-1 flex gap-1.5">
          <Button size="sm" variant="ghost" className="flex-1" icon={theme === 'dark' ? Sun : Moon} onClick={toggle}>
            {theme === 'dark' ? 'Claro' : 'Oscuro'}
          </Button>
          <Button size="sm" variant="ghost" className="flex-1" icon={LogOut} onClick={() => void logout()}>
            Salir
          </Button>
        </div>
      </div>
    </nav>
  )

  return (
    <div className="flex min-h-screen">
      {/* Desktop sidebar */}
      <aside
        className="hidden w-[248px] shrink-0 border-r lg:block"
        style={{ background: 'var(--surface-raised)', borderColor: 'var(--border)' }}
      >
        <div className="sticky top-0 h-screen">{sidebar}</div>
      </aside>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0" style={{ background: 'rgb(10 15 13 / 0.5)' }}
            onClick={() => setMobileOpen(false)} />
          <div
            className="absolute inset-y-0 left-0 w-[268px] border-r"
            style={{ background: 'var(--surface-raised)', borderColor: 'var(--border)' }}
          >
            {sidebar}
          </div>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header
          className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b px-4 lg:hidden"
          style={{ background: 'var(--surface-raised)', borderColor: 'var(--border)' }}
        >
          <Button size="sm" variant="ghost" icon={mobileOpen ? X : Menu}
            onClick={() => setMobileOpen((v) => !v)} aria-label="Menú" />
          <Wordmark compact />
        </header>

        <main key={location.pathname} className="animate-in-up min-w-0 flex-1 p-4 sm:p-6 lg:p-8">
          {children}
        </main>
      </div>
    </div>
  )
}

/** Page heading used by every admin screen, so titles line up everywhere. */
export function PageHeader({
  title,
  description,
  actions,
  badge,
}: {
  title: string
  description?: string
  actions?: ReactNode
  badge?: ReactNode
}) {
  return (
    <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div>
        <div className="flex items-center gap-2.5">
          <h1 className="text-[22px] font-bold tracking-tight">{title}</h1>
          {badge}
        </div>
        {description && (
          <p className="mt-1 max-w-2xl text-[13.5px]" style={{ color: 'var(--text-muted)' }}>
            {description}
          </p>
        )}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  )
}

/** Shown wherever seeded content is on screen. */
export function DemoNotice() {
  return (
    <div
      className="mb-5 flex items-start gap-2.5 rounded-lg border px-3.5 py-2.5 text-[12.5px]"
      style={{
        borderColor: 'var(--color-gold-300)',
        background: 'var(--color-gold-100)',
        color: 'var(--color-gold-700)',
      }}
    >
      <Badge tone="neutral" className="!bg-white/60 !text-[var(--color-gold-700)]">Demo</Badge>
      <span>
        Este entorno contiene datos de demostración. Ningún registro mostrado corresponde a
        información real de la Universidad de Colima.
      </span>
    </div>
  )
}

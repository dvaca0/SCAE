import { useState, type FormEvent } from 'react'
import { ShieldCheck, TriangleAlert } from 'lucide-react'
import { Button, Field, Input } from '../components/ui'
import { Logo } from '../components/Brand'
import { useAuth } from '../lib/auth'
import { ApiError } from '../lib/api'

export function Login() {
  const { login } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(username.trim(), password)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'No se pudo conectar con el servidor.')
      setBusy(false)
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.05fr_1fr]">
      {/* Institutional panel */}
      <div
        className="relative hidden flex-col justify-between overflow-hidden p-12 lg:flex"
        style={{ background: 'var(--color-uc-800)' }}
      >
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 opacity-[0.13]"
          style={{
            backgroundImage:
              'linear-gradient(var(--color-uc-200) 1px, transparent 1px), linear-gradient(90deg, var(--color-uc-200) 1px, transparent 1px)',
            backgroundSize: '52px 52px',
          }}
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -right-24 -top-24 h-80 w-80 rounded-full"
          style={{ background: 'radial-gradient(circle, rgb(198 147 10 / 0.20), transparent 68%)' }}
        />

        <div className="relative flex items-center gap-3">
          <Logo size={38} />
          <div className="leading-tight text-white">
            <div className="text-[19px] font-bold tracking-tight">SCAE</div>
            <div className="text-[11px] font-medium uppercase tracking-[0.16em] text-white/60">
              Universidad de Colima
            </div>
          </div>
        </div>

        <div className="relative max-w-md">
          <h1 className="text-[30px] font-bold leading-[1.15] tracking-tight text-white">
            Control de acceso vehicular institucional
          </h1>
          <p className="mt-4 text-[14px] leading-relaxed text-white/70">
            Reconocimiento de placas, verificación de permisos y registro auditable de
            cada entrada y salida en los accesos del campus.
          </p>
          <ul className="mt-8 space-y-3">
            {[
              'Decisión de acceso trazable y auditable',
              'Consola operativa para caseta',
              'Permisos por campus, acceso y vigencia',
            ].map((item) => (
              <li key={item} className="flex items-center gap-2.5 text-[13.5px] text-white/85">
                <ShieldCheck size={16} className="shrink-0 text-[var(--color-gold-300)]" />
                {item}
              </li>
            ))}
          </ul>
        </div>

        <div className="relative flex items-center justify-between">
          <span className="text-[10.5px] font-medium uppercase tracking-[0.2em] text-white/45">
            Estudia · Lucha · Trabaja
          </span>
          <span className="text-[11px] text-white/35">v2.0</span>
        </div>
      </div>

      {/* Form */}
      <div className="flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-[360px]">
          <div className="mb-8 lg:hidden">
            <Logo size={36} />
          </div>

          <h2 className="text-[22px] font-bold tracking-tight">Iniciar sesión</h2>
          <p className="mt-1.5 text-[13.5px]" style={{ color: 'var(--text-muted)' }}>
            Accede con tu cuenta institucional de SCAE.
          </p>

          <form onSubmit={onSubmit} className="mt-7 space-y-4" noValidate>
            <Field label="Usuario" required>
              <Input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                autoFocus
                required
                placeholder="usuario"
              />
            </Field>
            <Field label="Contraseña" required>
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
                placeholder="••••••••"
              />
            </Field>

            {error && (
              <div
                role="alert"
                className="flex items-start gap-2 rounded-lg px-3 py-2.5 text-[13px]"
                style={{ background: 'var(--deny-bg)', color: 'var(--deny)' }}
              >
                <TriangleAlert size={15} className="mt-px shrink-0" />
                {error}
              </div>
            )}

            <Button type="submit" variant="primary" size="lg" full loading={busy}>
              Entrar
            </Button>
          </form>

          <div
            className="mt-8 rounded-lg border border-dashed px-3.5 py-3 text-[12px] leading-relaxed"
            style={{ color: 'var(--text-muted)' }}
          >
            <strong className="font-semibold" style={{ color: 'var(--color-gold-700)' }}>
              Entorno de demostración.
            </strong>{' '}
            Los datos cargados no corresponden a registros reales de la Universidad de
            Colima. Cuentas de prueba: <code className="plate">admin</code>,{' '}
            <code className="plate">supervisor</code>, <code className="plate">guardia1</code>,{' '}
            <code className="plate">auditor</code>.
          </div>
        </div>
      </div>
    </div>
  )
}

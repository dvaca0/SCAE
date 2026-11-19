import { FileQuestion } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { Button, Card, EmptyState } from '../components/ui'

export function NotFound() {
  const navigate = useNavigate()
  return (
    <Card padded={false}>
      <EmptyState
        icon={FileQuestion}
        title="Página no encontrada"
        description="La ruta que intentas abrir no existe en SCAE."
        action={<Button variant="primary" onClick={() => navigate('/panel')}>Ir al panel</Button>}
      />
    </Card>
  )
}

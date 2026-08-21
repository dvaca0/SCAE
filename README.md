# SCAE — Sistema de Control de Acceso Vehicular

Plataforma institucional de control de acceso vehicular: reconocimiento de placas,
verificación de permisos, decisión auditable y registro de cada entrada y salida en
los accesos de la Universidad de Colima.

> **Entorno de demostración.** Los datos que se cargan con `make seed` están marcados
> como `demo` en la base y en la interfaz. **No corresponden a registros reales de la
> Universidad de Colima.**

---

## Qué resuelve

Un vehículo llega a una caseta. En segundos hay que decidir si pasa, y esa decisión
tiene que quedar registrada con su justificación. SCAE cubre esa cadena completa:

```
VEHÍCULO LLEGA → CAPTURA → DETECCIÓN → LPR → NORMALIZACIÓN
    → CONSULTA DE AUTORIZACIÓN → DECISIÓN
    → ABRIR / DENEGAR / REVISIÓN MANUAL → REGISTRO → AUDITORÍA
```

Cada eslabón es un módulo separado, de modo que la cámara, el modelo de visión, la
política de acceso y la barrera física puedan sustituirse de forma independiente.

## Arquitectura

```
frontend/                 React 18 + TypeScript + Vite + Tailwind 4
  ├── Consola de caseta   Interfaz operativa para el guardia
  └── Consola de gestión  Panel, padrón, permisos, infraestructura, auditoría
        │  HTTP + cookie de sesión httpOnly + CSRF
        ▼
backend/app/
  ├── api/                Rutas FastAPI y control de acceso por rol
  ├── services/
  │     ├── decision.py   Motor de decisión (función pura, sin E/S)
  │     ├── access.py     Orquestación del evento de acceso
  │     ├── audit.py      Registro append-only
  │     └── notifications.py
  ├── recognition/
  │     ├── pipeline.py   Detección y OCR tras protocolos intercambiables
  │     └── plate.py      Normalización de placas mexicanas
  ├── hardware/drivers.py Cámaras y barreras tras una interfaz por driver
  └── models/             Dominio SQLAlchemy 2.0 (15 tablas)
        │
        ▼
      SQLite (desarrollo) · PostgreSQL (producción, vía Alembic)
```

**Separación deliberada:** detección ≠ reconocimiento ≠ identidad ≠ autorización ≠
decisión ≠ actuación física ≠ registro. El prototipo original mezclaba las siete
dentro de un mismo endpoint.

## Requisitos

- macOS o Linux · Python 3.11 · Node 20+
- La primera ejecución del pipeline descarga ~100 MB de pesos YOLO y los modelos de
  EasyOCR. Con `SCAE_RECOGNITION_BACKEND=stub` la plataforma arranca sin ellos.

## Puesta en marcha

```bash
python3.11 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env          # genera SCAE_SECRET_KEY
cd frontend && npm install && cd ..

make migrate                  # crea el esquema
make seed                     # datos de demostración
```

Después, en dos terminales:

```bash
make api    # http://localhost:8000  ·  documentación en /api/docs
make web    # http://localhost:5173
```

Para detener ambos: `make stop`.

### Cuentas de demostración

| Usuario | Contraseña | Rol | Ve |
|---|---|---|---|
| `admin` | `SCAEadmin2026!` | Administrador | Todo, incluidos usuarios |
| `supervisor` | `SCAEsuper2026!` | Supervisor | Operación y padrón, sin usuarios |
| `guardia1` | `SCAEguard2026!` | Guardia | Sólo la consola de su carril |
| `auditor` | `SCAEaudit2026!` | Auditor | Sólo lectura, incluida la auditoría |

Todas exigen cambio de contraseña en el primer acceso. **Cámbialas antes de cualquier
despliegue real.**

## Roles

| Rol | Alcance |
|---|---|
| **Administrador** | Configuración, infraestructura, usuarios, todo el padrón |
| **Supervisor** | Operación, padrón, permisos, incidencias — sin gestión de usuarios |
| **Guardia** | Consola operativa de su carril asignado; no accede a la administración |
| **Auditor** | Lectura completa, incluida la traza de auditoría; sin escritura |

## Motor de decisión

Las reglas se evalúan en orden fijo, de la más restrictiva a la menos:

1. Sin placa legible → **revisión manual**
2. Placa en lista de control → **denegar** (prevalece sobre cualquier permiso)
3. Confianza de lectura bajo el umbral → **revisión manual**
4. Vehículo no registrado → **revisión manual** *(no es una denegación: las casetas
   reciben visitantes y proveedores constantemente)*
5. Sin permiso vigente → **denegar**
6. Permiso suspendido o revocado → **denegar**
7. Permiso fuera de vigencia → **denegar**
8. Permiso que no cubre ese acceso → **denegar**
9. En caso contrario → **permitir**

`services/decision.py` es una función pura sin efectos: se puede probar, reutilizar
desde una cámara automática y auditar sin levantar la aplicación.

## Hardware

Ningún componente habla directamente con un dispositivo. Todo pasa por
`hardware/drivers.py`, y cada cámara o controlador declara su `driver` y su `config`
en la base de datos.

| Driver | Tipo | Estado |
|---|---|---|
| `mock` | Cámara / barrera | **Implementado** — simula, y lo marca como simulado |
| `file` | Cámara | **Implementado** — reproduce imagen o vídeo local |
| `rtsp` | Cámara | Declarado, sin implementar |
| `unifi_protect` | Cámara | Declarado, sin implementar |
| `http_relay` | Barrera | Declarado, sin implementar |
| `gpio` | Barrera | Declarado, sin implementar |

Añadir UniFi Protect o un relé en Raspberry Pi es escribir una clase y registrarla:
no exige cambios de esquema, de API ni del motor de decisión. La pantalla **Estado del
sistema** indica en todo momento qué está operativo y qué está simulado.

## Comandos

```bash
make          # ayuda
make api      # API en :8000
make web      # interfaz en :5173
make seed     # datos demo
make test     # suite de pruebas
make migrate  # migraciones
make lint     # tipos del frontend
make stop     # detiene todo
make legacy   # prototipo v1 (Flask, :5001)
```

## Prototipo v1

`app.py`, `dashboard/dashboard.py` y `templates/` son la versión original y se
conservan intactos mientras se valida la v2. No comparten base de datos con la v2.

## Licencia

MIT — ver [LICENSE](LICENSE).

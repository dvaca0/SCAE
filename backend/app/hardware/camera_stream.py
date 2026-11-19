"""Vista en vivo de la cámara: un único proceso de captura compartido.

PARCHE LOCAL (Raspberry Pi 5) — ver /opt/scae/PATCHES.md

Qué resuelve
------------
El navegador no puede reproducir RTSPS, y la URL del flujo lleva un token que no
debe salir del servidor. Así que el backend se conecta una vez a la cámara,
decodifica, y reparte fotogramas JPEG a quien tenga sesión en SCAE.

Decisiones y por qué
--------------------
* **Un solo proceso de FFmpeg para todos los espectadores.** Si cada petición
  HTTP abriera su propia conexión RTSPS, tres guardias mirando serían tres
  decodificaciones de 4K en paralelo: ~3 núcleos de los 4 que tiene la Pi, con
  la plataforma de la tesis al lado. Aquí el coste es el mismo con uno o con
  diez espectadores.
* **Arranca con el primer espectador y para sola.** Mientras nadie mira, el
  consumo es cero. La decodificación de 4K HEVC cuesta ~93 % de un núcleo
  (medido), y el decodificador por hardware de la Pi 5 no es utilizable desde
  este FFmpeg, así que no se deja corriendo "por si acaso".
* **Sólo se guarda el último fotograma.** Nada de colas: si un espectador va
  lento, se pierde fotogramas, no acumula memoria ni latencia.
* **MJPEG** en vez de WebRTC o HLS. Lo pinta un `<img>` nativo, sin librerías,
  sin negociación y sin los segundos de latencia de HLS. Para una vista de
  caseta es lo que menos consume y menos puede romperse.
* **El token nunca se registra.** `_redact()` limpia cualquier URL antes de que
  llegue al log, y el estado que se expone por la API no incluye la URL.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

CONFIG_FILE = Path("/opt/scae/config/camera.env")

_URL_RE = re.compile(r"rtsps?://\S*")
SOI = b"\xff\xd8\xff"   # inicio de JPEG
EOI = b"\xff\xd9"       # fin de JPEG
MAX_FRAME_BYTES = 8 * 1024 * 1024

# Al conectar, el decodificador HEVC emite fotogramas grises hasta que llega el
# primer keyframe, porque intenta decodificar en mitad del GOP sin referencia
# ("Could not find ref with POC N"). Con un GOP de 5 s y 2 fps son ~10 grises
# seguidos: el guardia pulsaría "Ver en vivo" y vería cinco segundos de nada.
#
# Se descartan por TAMAÑO, no decodificando: medido en esta cámara, los grises
# pesan 3.4-6.1 KB y los reales ~47 KB, un factor de diez. Decodificar cada
# JPEG sólo para comprobarlo costaría CPU que esta Pi no debe gastar.
#
# El filtro se aplica SÓLO hasta el primer fotograma bueno de cada sesión de
# FFmpeg: después se acepta todo, para no tirar nunca una escena real que
# resulte ser muy uniforme (niebla, pared, oscuridad total).
MIN_PRIMING_BYTES = 12_000
PRIMING_GIVE_UP_SECONDS = 12.0


def _redact(text: str) -> str:
    """Nunca dejar que una URL con token llegue a un log."""
    return _URL_RE.sub("rtsps://<oculto>", text or "")


def _load_config() -> dict[str, str]:
    """Lee /opt/scae/config/camera.env (modo 640 root:scae).

    Se lee aquí y no por `EnvironmentFile=` de systemd para no tener que tocar
    la unidad del servicio, y para que la credencial viva en un solo sitio.
    """
    cfg: dict[str, str] = {}
    try:
        for line in CONFIG_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    except OSError as exc:
        logger.warning("No se pudo leer %s: %s", CONFIG_FILE, exc)
    # Las variables de entorno ganan, para poder probar sin tocar el fichero.
    for k in list(cfg) + [
        "SCAE_CAMERA_RTSP_URL", "SCAE_CAMERA_NAME", "SCAE_CAMERA_TRANSPORT",
        "SCAE_CAMERA_PREVIEW_FPS", "SCAE_CAMERA_PREVIEW_WIDTH",
        "SCAE_CAMERA_PREVIEW_QUALITY", "SCAE_CAMERA_IDLE_TIMEOUT",
    ]:
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


@dataclass
class StreamStatus:
    state: str = "stopped"          # stopped|connecting|live|reconnecting|error
    detail: str = ""
    viewers: int = 0
    frames: int = 0
    last_frame_at: Optional[float] = None
    started_at: Optional[float] = None
    reconnects: int = 0
    fps: float = 0.0
    width: Optional[int] = None
    height: Optional[int] = None
    _recent: list[float] = field(default_factory=list)


class CameraStream:
    """Captura compartida. Hilo único, FFmpeg único, último fotograma único."""

    def __init__(self) -> None:
        self._cfg = _load_config()
        self._lock = threading.Lock()
        self._frame: Optional[bytes] = None
        self._frame_seq = 0
        self._new_frame = threading.Condition(self._lock)
        self._viewers = 0
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        # Se reinicia en cada sesion de FFmpeg: ver MIN_PRIMING_BYTES.
        self._primed = False
        self._session_start = 0.0
        self.status = StreamStatus()

    # --- configuración -----------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self._cfg.get("SCAE_CAMERA_RTSP_URL"))

    @property
    def camera_name(self) -> str:
        return self._cfg.get("SCAE_CAMERA_NAME", "Cámara")

    def _int(self, key: str, default: int) -> int:
        try:
            return int(self._cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    def _ffmpeg_cmd(self) -> list[str]:
        url = self._cfg["SCAE_CAMERA_RTSP_URL"]
        fps = self._int("SCAE_CAMERA_PREVIEW_FPS", 2)
        width = self._int("SCAE_CAMERA_PREVIEW_WIDTH", 960)
        quality = self._int("SCAE_CAMERA_PREVIEW_QUALITY", 6)
        transport = self._cfg.get("SCAE_CAMERA_TRANSPORT", "tcp")
        threads = self._int("SCAE_CAMERA_DECODE_THREADS", 2)
        lowpower = self._cfg.get("SCAE_CAMERA_PREVIEW_MODE", "full") == "keyframes"
        return [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            # Techo de hilos del decodificador. Sin el, FFmpeg reparte la
            # decodificacion de 4K HEVC entre los 4 nucleos y el consumo de
            # corriente dispara la subtension de la placa: medidos cuatro
            # episodios "Undervoltage detected!" en un minuto de vista en vivo,
            # con el collector de la tesis reportando undervolt=True. Con dos
            # hilos se pierden fotogramas antes que estabilidad electrica, que
            # es el cambio correcto en una Pi compartida con la tesis.
            "-threads", str(threads),
            "-rtsp_transport", transport,
            "-timeout", "15000000",
            "-fflags", "nobuffer", "-flags", "low_delay",
            # Modo "keyframes": decodifica SOLO fotogramas intra. Medido, baja
            # de ~105% de un nucleo a ~6%, pero la camara solo emite uno cada
            # 5 s (GOP de 150 a 30 fps), asi que la vista va a 0.2 fps. Es el
            # modo a usar si la vista en vivo vuelve a provocar subtension:
            # la plataforma de la tesis tiene prioridad sobre la comodidad de
            # ver video fluido.
            *(["-skip_frame", "nokey"] if lowpower else []),
            "-i", url,
            "-map", "0:v:0", "-an",
            # scale a ancho par (-2 conserva la proporción y evita alturas impares)
            # En modo keyframes HAY QUE limitar el ritmo igual: sin el filtro
            # fps, FFmpeg vuelca los intra tan rapido como puede y el coste se
            # traslada al codificador JPEG. Medido: 818 fotogramas en 30 s y un
            # 24% de nucleo, cuando lo que se buscaba era ~6%.
            "-vf", (f"fps=1/5,scale={width}:-2" if lowpower
                     else f"fps={fps},scale={width}:-2"),
            "-f", "mjpeg", "-q:v", str(quality),
            "-",
        ]

    # --- ciclo de vida -----------------------------------------------------
    def acquire(self) -> None:
        """Un espectador más. Arranca la captura si era el primero.

        El hilo se crea DENTRO del lock a propósito. Comprobar fuera y arrancar
        después abría una carrera: tres peticiones simultáneas llegaban a la vez
        a "no hay hilo" y arrancaban tres capturas, cada una con su FFmpeg y
        peleándose por escribir el estado. Se vio en pruebas: tres clientes
        dejaron tres hilos y un proceso huérfano.
        """
        with self._lock:
            self._viewers += 1
            self.status.viewers = self._viewers
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(
                    target=self._run, name="scae-camera", daemon=True
                )
                self._thread.start()

    def release(self) -> None:
        with self._lock:
            self._viewers = max(0, self._viewers - 1)
            self.status.viewers = self._viewers

    def stop(self) -> None:
        self._stop.set()
        self._kill_proc()

    def _kill_proc(self) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    # --- bucle de captura --------------------------------------------------
    def _run(self) -> None:
        backoff = 1.0
        idle_timeout = self._int("SCAE_CAMERA_IDLE_TIMEOUT", 20)
        while not self._stop.is_set():
            if self._viewers == 0:
                # Nadie mira: parar y no volver hasta que alguien vuelva.
                self._set_state("stopped", "Sin espectadores")
                return
            try:
                self._primed = False
                self._session_start = time.monotonic()
                self._set_state("connecting", "Conectando con la cámara")
                self._proc = subprocess.Popen(
                    self._ffmpeg_cmd(),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    bufsize=0,
                )
                self._pump(idle_timeout)
                backoff = 1.0
            except FileNotFoundError:
                self._set_state("error", "FFmpeg no está instalado")
                return
            except Exception as exc:  # pragma: no cover - depende del entorno
                self._set_state("error", _redact(str(exc))[:160])
            finally:
                self._kill_proc()

            if self._stop.is_set() or self._viewers == 0:
                break
            self.status.reconnects += 1
            self._set_state("reconnecting", f"Reintento en {backoff:.0f} s")
            # Backoff exponencial con techo: un corte de red no debe convertirse
            # en un bucle de reconexión que consuma CPU. La espera se trocea en
            # medio segundo para notar enseguida que ya no queda nadie mirando,
            # en vez de seguir dormido hasta 30 s con la cámara caída.
            waited = 0.0
            while waited < backoff and not self._stop.is_set() and self._viewers > 0:
                self._stop.wait(0.5)
                waited += 0.5
            backoff = min(backoff * 2, 30.0)

        self._set_state("stopped", "Captura detenida")

    def _pump(self, idle_timeout: int) -> None:
        """Lee JPEGs concatenados del stdout de FFmpeg."""
        assert self._proc and self._proc.stdout
        buf = bytearray()
        last_frame = time.monotonic()
        idle_since: Optional[float] = None

        while not self._stop.is_set():
            if self._viewers == 0:
                if idle_since is None:
                    idle_since = time.monotonic()
                elif time.monotonic() - idle_since > idle_timeout:
                    logger.info("Vista en vivo detenida: nadie mirando")
                    return
            else:
                idle_since = None

            chunk = self._proc.stdout.read(65536)
            if not chunk:
                err = b""
                if self._proc.stderr:
                    try:
                        err = self._proc.stderr.read() or b""
                    except Exception:
                        pass
                msg = _redact(err.decode("utf-8", "replace")).strip().splitlines()
                self._set_state("error", (msg[-1] if msg else "FFmpeg terminó")[:160])
                return

            buf.extend(chunk)
            if len(buf) > MAX_FRAME_BYTES:
                # Flujo desincronizado: tirar y volver a buscar un SOI.
                i = buf.rfind(SOI)
                del buf[: i if i > 0 else len(buf)]

            while True:
                start = buf.find(SOI)
                if start < 0:
                    break
                end = buf.find(EOI, start + 3)
                if end < 0:
                    if start > 0:
                        del buf[:start]
                    break
                frame = bytes(buf[start : end + 2])
                del buf[: end + 2]
                self._publish(frame)
                last_frame = time.monotonic()

            if time.monotonic() - last_frame > 20:
                self._set_state("error", "Sin fotogramas durante 20 s")
                return

    def _publish(self, frame: bytes) -> None:
        if not self._primed:
            waited = time.monotonic() - self._session_start
            if len(frame) < MIN_PRIMING_BYTES and waited < PRIMING_GIVE_UP_SECONDS:
                return  # gris de arranque: no se publica
            self._primed = True
        now = time.time()
        with self._lock:
            self._frame = frame
            self._frame_seq += 1
            s = self.status
            s.frames += 1
            s.last_frame_at = now
            if s.state != "live":
                s.state = "live"
                s.detail = "Transmitiendo"
                s.started_at = s.started_at or now
            s._recent.append(now)
            if len(s._recent) > 20:
                del s._recent[:-20]
            if len(s._recent) > 1:
                span = s._recent[-1] - s._recent[0]
                s.fps = round((len(s._recent) - 1) / span, 2) if span > 0 else 0.0
            if s.width is None:
                dims = _jpeg_size(frame)
                if dims:
                    s.width, s.height = dims
            self._new_frame.notify_all()

    def _set_state(self, state: str, detail: str) -> None:
        with self._lock:
            self.status.state = state
            self.status.detail = detail
        if state in {"error", "reconnecting"}:
            logger.warning("Vista en vivo: %s · %s", state, _redact(detail))
        else:
            logger.info("Vista en vivo: %s · %s", state, _redact(detail))

    # --- consumo -----------------------------------------------------------
    def peek(self) -> tuple[int, Optional[bytes]]:
        """Número de secuencia y último fotograma, sin bloquear.

        Lo usa el generador asíncrono de la ruta: sondear desde asyncio permite
        preguntar a Starlette si el cliente sigue conectado, cosa que un
        generador síncrono bloqueado en un `wait()` no puede hacer.
        """
        with self._lock:
            return self._frame_seq, self._frame

    def latest(self, timeout: float = 10.0) -> Optional[bytes]:
        """Último fotograma, esperando a que llegue uno si hace falta."""
        deadline = time.monotonic() + timeout
        with self._lock:
            while self._frame is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._new_frame.wait(remaining)
            return self._frame

    def frames(self, boundary: str, starve_timeout: float = 15.0) -> Iterator[bytes]:
        """Generador multipart/x-mixed-replace. Sólo entrega fotogramas nuevos:
        si el cliente va lento se salta los intermedios en vez de acumularlos.

        `starve_timeout` no es un detalle menor. Este generador es síncrono y
        corre en el threadpool: mientras está bloqueado esperando un fotograma,
        Starlette **no puede cerrarlo** cuando el cliente se va, porque sólo
        puede hacerlo en un `yield`. Sin un tope, un cliente que se desconecta
        con la cámara caída dejaba su espectador contado para siempre, el hilo
        de captura no veía nunca cero espectadores y reintentaba sin fin,
        dejando un FFmpeg huérfano. Al rendirse tras unos segundos sin imagen,
        el generador termina, se ejecuta el `finally` y la cuenta se libera.
        """
        self.acquire()
        last_seq = -1
        try:
            while not self._stop.is_set():
                deadline = time.monotonic() + starve_timeout
                frame = None
                with self._lock:
                    while self._frame_seq == last_seq:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        self._new_frame.wait(min(remaining, 1.0))
                    if self._frame_seq != last_seq:
                        frame = self._frame
                        last_seq = self._frame_seq
                if frame is None:
                    # Sin imagen en `starve_timeout`: cerrar la respuesta. El
                    # cliente lo detecta y reintenta; el servidor no se queda
                    # con un espectador fantasma.
                    return
                yield (
                    f"--{boundary}\r\n"
                    f"Content-Type: image/jpeg\r\n"
                    f"Content-Length: {len(frame)}\r\n\r\n"
                ).encode() + frame + b"\r\n"
        finally:
            self.release()


def _jpeg_size(data: bytes) -> Optional[tuple[int, int]]:
    """Ancho y alto leídos de las cabeceras SOFn, sin decodificar la imagen."""
    i = 2
    n = len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h = int.from_bytes(data[i + 5 : i + 7], "big")
            w = int.from_bytes(data[i + 7 : i + 9], "big")
            return (w, h) if w and h else None
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg = int.from_bytes(data[i + 2 : i + 4], "big")
        if seg <= 0:
            return None
        i += 2 + seg
    return None


_stream: Optional[CameraStream] = None
_stream_lock = threading.Lock()


def get_stream() -> CameraStream:
    global _stream
    with _stream_lock:
        if _stream is None:
            _stream = CameraStream()
        return _stream

"""Outbound notifications.

The prototype sent infraction mail to a hard-coded `person@example.com` and
raised if SMTP was unset, taking the whole request down with it. Here a
notification is best-effort and its outcome is recorded on the incident, so
the UI can tell an operator that the email did not go out.
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Optional

logger = logging.getLogger(__name__)


def _smtp_config() -> tuple[str, int, str | None, str | None, str | None]:
    return (
        os.environ.get("SMTP_HOST", ""),
        int(os.environ.get("SMTP_PORT", "587")),
        os.environ.get("SMTP_USER"),
        os.environ.get("SMTP_PASSWORD"),
        os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER"),
    )


def smtp_configured() -> bool:
    host, _port, user, password, _from = _smtp_config()
    return bool(host and user and password and "example.com" not in host)


def send_email(to_email: str, subject: str, body: str) -> tuple[bool, Optional[str]]:
    """Returns (sent, error). Never raises into the request path."""
    if not to_email:
        return False, "La persona no tiene correo registrado."
    if not smtp_configured():
        return False, "SMTP no está configurado en este entorno."

    host, port, user, password, sender = _smtp_config()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to_email
    message.set_content(body)
    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(message)
        return True, None
    except Exception as exc:  # noqa: BLE001 - notification must not break access control
        logger.warning("Fallo al enviar notificación a %s: %s", to_email, exc)
        return False, f"Error SMTP: {exc}"[:250]


def notify_incident(incident, person) -> tuple[bool, Optional[str]]:
    if person is None:
        return False, "La incidencia no está asociada a una persona."
    body = (
        f"Estimado/a {person.full_name}:\n\n"
        f"Se ha registrado una incidencia relacionada con su acceso vehicular.\n\n"
        f"Tipo: {incident.incident_type.value}\n"
        f"Asunto: {incident.title}\n"
        f"Detalle: {incident.description or 'Sin detalle adicional.'}\n\n"
        f"Para cualquier aclaración acuda a la coordinación de seguridad "
        f"del campus.\n\n"
        f"SCAE — Sistema de Control de Acceso Vehicular"
    )
    return send_email(person.email or "", f"SCAE · {incident.title}", body)

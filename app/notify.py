"""Email delivery over SMTP. Telegram / web push can be added as sibling modules."""
from __future__ import annotations

import html
import smtplib
from email.message import EmailMessage

from .config import Settings


def render_text(payload: dict) -> str:
    lines = [f"WorldPulse AI - {payload['edition'].title()} briefing",
             "Summaries are AI-generated and may contain errors. Read the original article.", ""]
    for sec in payload["sections"]:
        lines.append(sec["label"].upper())
        for s in sec["stories"]:
            head = (s.get("summary") or {}).get("headline_en") or s["title"]
            lines.append(f"- {head}  [{s['publisher']}, {s['published_at']}]\n  {s['url']}")
        lines.append("")
    return "\n".join(lines)


def render_html(payload: dict) -> str:
    parts = [f"<h2>WorldPulse AI - {html.escape(payload['edition'].title())} briefing</h2>",
             "<p><em>Summaries are AI-generated and may contain errors. Read the original article.</em></p>"]
    for sec in payload["sections"]:
        parts.append(f"<h3>{html.escape(sec['label'])}</h3><ul>")
        for s in sec["stories"]:
            head = html.escape((s.get("summary") or {}).get("headline_en") or s["title"])
            url = html.escape(s["url"], quote=True)
            parts.append(f'<li><a href="{url}">{head}</a> &mdash; {html.escape(s["publisher"])}</li>')
        parts.append("</ul>")
    return "\n".join(parts)


def deliver_email(settings: Settings, recipient: str, payload: dict, http=None) -> tuple[str, str]:
    """Send one briefing email. Returns (status, detail); never raises."""
    if not settings.smtp_host or not settings.email_from:
        return "skipped", "SMTP not configured"
    msg = EmailMessage()
    msg["Subject"] = f"WorldPulse AI {payload['edition'].title()} briefing"
    msg["From"] = settings.email_from
    msg["To"] = recipient
    msg.set_content(render_text(payload))
    msg.add_alternative(render_html(payload), subtype="html")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        return "sent", "ok"
    except (smtplib.SMTPException, OSError) as exc:
        from .security import redact
        return "failed", redact(f"{type(exc).__name__}: {exc}")[:500]

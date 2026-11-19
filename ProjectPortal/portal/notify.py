"""In-app notifications (always) + optional email (only if SMTP is configured)."""
import smtplib
import threading
from email.message import EmailMessage

import config


def _send_email(subject, body, recipients):
    if not config.SMTP_HOST or not recipients:
        return

    def work():
        try:
            msg = EmailMessage()
            msg["Subject"], msg["From"], msg["To"] = subject, config.MAIL_FROM, ", ".join(recipients)
            msg.set_content(body)
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as s:
                if config.SMTP_USER:
                    s.starttls()
                    s.login(config.SMTP_USER, config.SMTP_PASSWORD or "")
                s.send_message(msg)
        except Exception as exc:                      # never break a request over email
            print(f"[notify] email failed: {exc}")

    threading.Thread(target=work, daemon=True).start()


def notify_admins(conn, message, link=None, subject="Kitting Portal"):
    admins = conn.execute("SELECT id, email FROM users WHERE role='admin' AND active=1").fetchall()
    for a in admins:
        conn.execute("INSERT INTO notifications(user_id,message,link) VALUES(?,?,?)", (a["id"], message, link))
    emails = [a["email"] for a in admins if a["email"]] + config.NOTIFY_EMAILS
    _send_email(subject, message, sorted(set(emails)))


def notify_user(conn, user_id, message, link=None, subject="Kitting Portal"):
    if not user_id:
        return
    conn.execute("INSERT INTO notifications(user_id,message,link) VALUES(?,?,?)", (user_id, message, link))
    row = conn.execute("SELECT email FROM users WHERE id=?", (user_id,)).fetchone()
    if row and row["email"]:
        _send_email(subject, message, [row["email"]])

"""⑧ Report agent: Excel + JSON + Markdown, then optional Email / Telegram / GitHub job summary."""
from __future__ import annotations

import logging
import os
import shutil
import smtplib
from email.message import EmailMessage
from pathlib import Path

from src.models import RunResult
from src.reports.excel import write_excel
from src.reports.json import write_json
from src.reports.markdown import render_markdown, write_markdown

log = logging.getLogger(__name__)

XLSX_MIME = ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def summary_lines(res: RunResult) -> list[str]:
    """Plain-text digest shared by email and Telegram."""
    r = res.regime
    lines = [(f"[{res.universe_name}, {res.universe_size} stocks] " if res.universe_name else "")
             + (res.session_label or f"Swing setups for the session after {res.as_of}"),
             f"Regime: {r.regime.upper()} (risk ×{r.exposure:g}) · breadth {r.breadth_pct:.0f}%", ""]
    for s in res.setups:
        grade = f" {s.breakout_grade}" if s.breakout_grade else ""
        lines.append(f"{s.rank}. {s.symbol} [{s.action} · {s.setup_type}{grade}] buy ≥ {s.entry_trigger:,.2f} · "
                     f"SL {s.stop:,.2f} ({s.stop_pct:.1f}%) · T1 {s.target1:,.2f} · T2 {s.target2:,.2f} · "
                     f"qty {s.quantity} · score {s.final_score:.0f}")
        if s.today_status:
            lines.append(f"     today: {s.today_status}")
    if not res.setups:
        lines.append("No setups passed every gate today.")
    if res.watchlist:
        lines += ["", "Watchlist: " + ", ".join(f"{c.symbol} ({c.final_score:.0f})" for c in res.watchlist[:8])]
    lines += ["", "Research only — not investment advice. Verify before trading."]
    return lines


def build_email(res: RunResult, sender: str, recipient: str, attachment: Path | None) -> EmailMessage:
    msg = EmailMessage()
    n = len(res.setups)
    tag = {"morning": "Morning", "afternoon": "Afternoon · PROVISIONAL", "eod": "Post-close"}.get(res.session, "")
    if res.universe_name:
        tag = f"{res.universe_name} · {tag}"
    msg["Subject"] = (f"Swing Agent [{tag}] {res.as_of}: {n} setup{'s' if n != 1 else ''} · "
                      f"{res.regime.regime.upper()} market")
    msg["From"], msg["To"] = sender, recipient
    body = "\n".join(summary_lines(res))
    if attachment and attachment.exists():
        body += "\n\nFull details in the attached Excel report."
    msg.set_content(body)
    if attachment and attachment.exists():
        msg.add_attachment(attachment.read_bytes(), maintype=XLSX_MIME[0], subtype=XLSX_MIME[1],
                           filename=f"swing_agent_{res.as_of}_{res.session}.xlsx")
    return msg


class ReportAgent:
    def __init__(self, settings: dict, out_dir: Path):
        self.cfg = settings["report"]
        self.out_dir = out_dir

    def run(self, res: RunResult) -> list[Path]:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        name = self.cfg.get("filename", "swing_agent")
        written: list[Path] = []
        xlsx = None
        if self.cfg.get("excel", True):
            xlsx = write_excel(res, self.out_dir / f"{name}.xlsx")
            written.append(xlsx)
        if self.cfg.get("json", True):
            written.append(write_json(res, self.out_dir / f"{name}.json"))
        if self.cfg.get("markdown", True):
            written.append(write_markdown(res, self.out_dir / f"{name}.md"))

        if self.cfg.get("archive_daily", False):
            arch = self.out_dir / "history" / res.as_of
            arch.mkdir(parents=True, exist_ok=True)
            keep = {f".{x.lstrip('.')}" for x in self.cfg.get("archive_formats", ["md", "xlsx", "json"])}
            for p in written:  # keep morning / afternoon / eod side by side
                if p.suffix in keep:
                    shutil.copy2(p, arch / f"{p.stem}_{res.session}{p.suffix}")

        for p in written:
            log.info("Report written: %s", p)
        self._github_summary(res)
        if self.cfg.get("email", True):
            self._email(res, xlsx)
        if self.cfg.get("telegram", True):
            self._telegram(res)
        return written

    @staticmethod
    def _github_summary(res: RunResult) -> None:
        path = os.getenv("GITHUB_STEP_SUMMARY")
        if path:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(render_markdown(res))

    @staticmethod
    def _email(res: RunResult, xlsx: Path | None) -> None:
        """Gmail SMTP with an App Password (same secrets as srikanth-stock-2)."""
        sender = (os.getenv("GMAIL_ADDRESS") or "").strip()
        # Google displays app passwords as "abcd efgh ijkl mnop"; the real password has no spaces,
        # and a pasted secret often carries a trailing newline — strip all whitespace.
        password = "".join((os.getenv("GMAIL_APP_PASSWORD") or "").split())
        if not (sender and password):
            return
        recipient = (os.getenv("RECIPIENT_EMAIL") or "").strip() or sender
        try:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
                server.starttls()
                server.login(sender, password)
                server.send_message(build_email(res, sender, recipient, xlsx))
            log.info("Email sent to %s", recipient)
        except Exception as exc:  # a mail failure must never fail the run
            hint = ""
            if "535" in str(exc):
                hint = (" — Gmail rejected the login: check GMAIL_ADDRESS is the account that created the "
                        "App Password, 2-Step Verification is on, and the App Password hasn't been revoked")
            log.warning("Email failed: %s%s", exc, hint)

    @staticmethod
    def _telegram(res: RunResult) -> None:
        token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
        if not (token and chat):
            return
        import requests

        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": "📈 " + "\n".join(summary_lines(res))}, timeout=15)
        except Exception as exc:
            log.warning("Telegram notification failed: %s", exc)

"""Formatted Excel workbook: Summary · Setups · Watchlist · Full Scan · Sectors · Evidence · Data Quality."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule, ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from src.models import RunResult

NAVY = "1F3864"
HEADER_FILL = PatternFill("solid", fgColor=NAVY)
HEADER_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=14, color=NAVY)
BOLD = Font(bold=True)
THIN = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
GREEN = PatternFill("solid", fgColor="E2EFDA")
RED = PatternFill("solid", fgColor="FCE4D6")
REGIME_FILL = {"bullish": "C6EFCE", "neutral": "FFEB9C", "bearish": "FFC7CE"}
ACTION_FILL = {"BUY NOW": "C6EFCE", "BUY": "DDEBF7", "WATCH": "FFF2CC", "AVOID": "FCE4D6", "SKIP": "F2F2F2"}

PRE_MARKET_CHECKLIST = [  # adapted from srikanth-stock-2
    "Nifty / Sensex not gapping down more than 0.5% in pre-open",
    "Stock's pre-open price is below the 'Don't chase above' level",
    "No overnight news, results or corporate action on the stock",
    "Pre-open volume shows interest (not a dead open)",
    "No upper-circuit / illiquidity risk",
    "Buy-stop placed at the Entry Trigger — not a market order",
    "Stop-loss order ready to place immediately after the fill",
    "Quantity matches the report (risk per trade unchanged)",
    "Sector is not broadly weak today",
    "Trade plan (entry, stop, T1, T2, time stop) logged before entering",
]

PRICE, PCT, INT, INR = "#,##0.00", "0.00", "#,##0", "₹#,##0"


def _table(ws: Worksheet, headers: Sequence[str], rows: Sequence[Sequence[Any]],
           formats: dict[str, str] | None = None, start_row: int = 1, max_width: int = 45) -> None:
    formats = formats or {}
    for j, h in enumerate(headers, 1):
        c = ws.cell(row=start_row, column=j, value=h)
        c.fill, c.font, c.border = HEADER_FILL, HEADER_FONT, BORDER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for i, row in enumerate(rows, start_row + 1):
        for j, v in enumerate(row, 1):
            if isinstance(v, float) and v != v:
                v = None
            c = ws.cell(row=i, column=j, value=v)
            c.border = BORDER
            fmt = formats.get(headers[j - 1])
            if fmt:
                c.number_format = fmt
    ws.freeze_panes = ws.cell(row=start_row + 1, column=3)
    if rows:
        ws.auto_filter.ref = f"A{start_row}:{get_column_letter(len(headers))}{start_row + len(rows)}"
    ws.row_dimensions[start_row].height = 32
    for j, h in enumerate(headers, 1):
        vals = [len(str(h))] + [len(f"{r[j - 1]:,.2f}" if isinstance(r[j - 1], float) else str(r[j - 1] or ""))
                                for r in rows[:300]]
        ws.column_dimensions[get_column_letter(j)].width = min(max(vals) + 2, max_width)


def _score_scale(ws: Worksheet, headers: Sequence[str], cols: Sequence[str], n_rows: int, start_row: int = 1):
    if n_rows == 0:
        return
    for col in cols:
        if col in headers:
            L = get_column_letter(headers.index(col) + 1)
            ws.conditional_formatting.add(
                f"{L}{start_row + 1}:{L}{start_row + n_rows}",
                ColorScaleRule(start_type="num", start_value=0, start_color="F8696B",
                               mid_type="num", mid_value=50, mid_color="FFEB84",
                               end_type="num", end_value=100, end_color="63BE7B"))


def _fill_actions(ws: Worksheet, headers: Sequence[str], n_rows: int, col: str = "Action") -> None:
    if col not in headers:
        return
    L = get_column_letter(headers.index(col) + 1)
    for i in range(2, n_rows + 2):
        cell = ws[f"{L}{i}"]
        if cell.value in ACTION_FILL:
            cell.fill = PatternFill("solid", fgColor=ACTION_FILL[cell.value])
            cell.font = BOLD


def _summary(wb: Workbook, res: RunResult) -> None:
    ws = wb.active
    ws.title = "Summary"
    r = res.regime
    ws["A1"] = "Srikanth Swing Trade Agent — Daily Report"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = f"Setups for the session after {res.as_of} · generated {res.generated_at}"
    ws["A2"].font = Font(italic=True, color="595959")
    items = [
        ("Run", res.session_label or res.session),
        ("Market regime", r.regime.upper()),
        ("Risk multiplier", r.exposure),
        ("Nifty 50 close", r.benchmark_close),
        ("Nifty 20D return %", r.benchmark_return_20d_pct),
        ("Breadth (% > 50-EMA)", r.breadth_pct),
        ("Universe scanned", f"{res.universe_name or 'Custom'} — {res.universe_size} stocks"),
        ("Passed data quality", sum(q.status != "FAIL" for q in res.quality)),
        ("Next-session setups", len(res.setups)),
        ("Watchlist", len(res.watchlist)),
        *[(f"Verdict: {a}", n) for a, n in _action_counts(res).items()],
        ("Capital (₹)", res.capital),
        ("Total ₹ at risk if all stops hit", sum(s.capital_at_risk for s in res.setups)),
        ("Data sources", ", ".join(res.data_sources)),
    ]
    for i, (k, v) in enumerate(items, 4):
        ws.cell(row=i, column=1, value=k).font = BOLD
        c = ws.cell(row=i, column=2, value=v)
        if k in ("Capital (₹)", "Total ₹ at risk if all stops hit"):
            c.number_format = INR
    ws["B5"].fill = PatternFill("solid", fgColor=REGIME_FILL.get(r.regime, "FFFFFF"))
    ws["B5"].font = BOLD
    if res.session == "afternoon":
        ws["B4"].fill = PatternFill("solid", fgColor="FFEB9C")
        ws["B4"].font = BOLD
    row = 4 + len(items) + 1
    ws.cell(row=row, column=1, value="Regime notes").font = BOLD
    for n in r.notes:
        row += 1
        ws.cell(row=row, column=1, value=f"• {n}")
    row += 2
    ws.cell(row=row, column=1, value="Execution rules").font = BOLD
    for line in [
        "Place a buy-stop at Entry Trigger; do not buy below it.",
        "Skip if the stock opens above the 'Don't chase above' price.",
        "Cancel if not triggered within the valid sessions shown.",
        "Place the stop-loss immediately after entry.",
        "Book part at T1 and trail the stop to entry; exit remainder at T2 or on time stop.",
    ]:
        row += 1
        ws.cell(row=row, column=1, value=f"• {line}")
    row += 2
    ws.cell(row=row, column=1, value=res.disclaimer).font = Font(italic=True, color="C00000")
    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 42


def _action_counts(res: RunResult) -> dict[str, int]:
    order = ["BUY NOW", "BUY", "WATCH", "AVOID", "SKIP"]
    fs = res.full_scan
    counts = fs["action"].value_counts().to_dict() if fs is not None and not fs.empty and "action" in fs else {}
    return {a: int(counts.get(a, 0)) for a in order}


def _setups(wb: Workbook, res: RunResult) -> None:
    ws = wb.create_sheet("Next Session Setups")
    headers = ["Rank", "Symbol", "Action", "Today (live)", "Last", "Sector", "Setup", "Grade", "Live Status",
               "Follow-through", "Close",
               "Entry Trigger", "Don't Chase Above", "Stop Loss", "Stop %", "Risk/Share", "Target 1", "Target 2",
               "R:R T1", "R:R T2", "Qty", "Position ₹", "₹ At Risk", "Room to 52W High (R)", "Valid Sessions",
               "Time Stop (sessions)", "Technical", "RS %ile", "Sector Score", "Fundamental", "Event",
               "Confluence", "Penalty", "Final Score", "Key Risks"]
    rows = [[s.rank, s.symbol, s.action, s.today_status or "—", s.today_last, s.sector, s.setup_type,
             s.breakout_grade, s.live_status, s.follow_through,
             s.close, s.entry_trigger, s.entry_limit, s.stop, s.stop_pct, s.risk_per_share, s.target1, s.target2,
             s.rr_t1, s.rr_t2, s.quantity, s.position_value, s.capital_at_risk, s.room_to_52w_high_r,
             s.valid_sessions, s.time_stop_sessions, s.technical_score, s.rs_percentile, s.sector_score,
             s.fundamental_score, s.event_score, s.confluence_score, s.penalty, s.final_score,
             " | ".join(s.risks)] for s in res.setups]
    fmts = {h: PRICE for h in ["Close", "Last", "Entry Trigger", "Don't Chase Above", "Stop Loss", "Risk/Share",
                                "Target 1", "Target 2"]}
    fmts.update({"Stop %": PCT, "Position ₹": INR, "₹ At Risk": INR, "Qty": INT})
    _table(ws, headers, rows, fmts)
    _score_scale(ws, headers, ["Technical", "RS %ile", "Sector Score", "Fundamental", "Event", "Confluence",
                               "Final Score"], len(rows))
    _fill_actions(ws, headers, len(rows))
    for col, fill in (("Entry Trigger", GREEN), ("Stop Loss", RED), ("Target 1", GREEN), ("Target 2", GREEN)):
        L = get_column_letter(headers.index(col) + 1)
        for i in range(2, len(rows) + 2):
            ws[f"{L}{i}"].fill = fill
            ws[f"{L}{i}"].font = BOLD
    if not rows:
        ws["A3"] = "No setups passed every gate today — see Watchlist for near-misses."


def _watchlist(wb: Workbook, res: RunResult) -> None:
    ws = wb.create_sheet("Watchlist")
    headers = ["Symbol", "Action", "Sector", "Setup", "Grade", "Follow-through", "Close", "Technical", "RS %ile",
               "Sector Score", "Fundamental", "Event", "Confluence", "Penalty", "Final Score", "Next Results",
               "Why not a setup", "Risks"]
    rows = [[c.symbol, c.action, c.sector, c.setup_type, c.breakout_grade, c.follow_through, c.close,
             c.technical_score, c.rs_percentile, c.sector_score, c.fundamental_score, c.event_score,
             c.confluence_score, c.penalty, c.final_score, (c.events or {}).get("next_earnings"),
             c.rejected_reason, " | ".join(c.risks)] for c in res.watchlist]
    _table(ws, headers, rows, {"Close": PRICE})
    _score_scale(ws, headers, ["Technical", "RS %ile", "Sector Score", "Fundamental", "Event", "Confluence",
                               "Final Score"], len(rows))
    _fill_actions(ws, headers, len(rows))


def _breakdown(wb: Workbook, res: RunResult) -> None:
    """stock-2 'Score Breakdown': how many points each component contributed."""
    ws = wb.create_sheet("Score Breakdown")
    comps = ["technical_pts", "relative_strength_pts", "fundamental_pts", "sector_pts", "event_pts", "confluence_pts"]
    headers = ["Symbol", "Status", "Action", "Technical", "Rel. Strength", "Fundamental", "Sector", "Event",
               "Confluence", "Weighted", "Penalty", "Final Score", "Confluence: Trend %", "Momentum %",
               "Overlap %", "Volume %", "Indicators Computed"]
    rows = []
    for status, items in (("SETUP", res.setups), ("WATCH", res.watchlist)):
        for x in items:
            b = x.score_breakdown or {}
            conf = getattr(x, "confluence", None) or {}
            rows.append([x.symbol, status, x.action, *[b.get(k) for k in comps], b.get("weighted"),
                         b.get("penalty"), x.final_score, conf.get("conf_trend"), conf.get("conf_momentum"),
                         conf.get("conf_overlap"), conf.get("conf_volume"), conf.get("indicators_computed")])
    _table(ws, headers, rows)
    _fill_actions(ws, headers, len(rows))
    _score_scale(ws, headers, ["Final Score", "Confluence: Trend %", "Momentum %", "Overlap %", "Volume %"], len(rows))
    note = len(rows) + 3
    ws.cell(row=note, column=1, value="Points = component score × its weight share. Weighted + Penalty = Final "
            "(clipped 0–100). Penalties: loss-making −8, failed breakout −15.").font = Font(italic=True)


def _checklist(wb: Workbook, res: RunResult) -> None:
    ws = wb.create_sheet("Pre-Market Checklist")
    headers = ["#", "Check before 9:15 AM", *[s.symbol for s in res.setups[:8]]]
    rows = [[i, item, *([""] * min(len(res.setups), 8))] for i, item in enumerate(PRE_MARKET_CHECKLIST, 1)]
    _table(ws, headers, rows, max_width=70)
    ws.freeze_panes = "C2"


def _dataframe(wb: Workbook, title: str, df: pd.DataFrame, score_cols: Sequence[str] = ()) -> None:
    ws = wb.create_sheet(title)
    if df is None or df.empty:
        ws["A1"] = "No data"
        return
    headers = [str(c) for c in df.columns]
    rows = df.astype(object).where(pd.notna(df), None).values.tolist()
    _table(ws, headers, rows)
    _score_scale(ws, headers, score_cols, len(rows))


def _evidence(wb: Workbook, res: RunResult) -> None:
    ws = wb.create_sheet("Evidence")
    headers = ["Symbol", "Status", "Rule", "Value", "Threshold", "Passed", "Note"]
    rows = []
    for s in res.setups:
        rows += [[s.symbol, "SETUP", e.rule, str(e.value), str(e.threshold), bool(e.passed), e.note] for e in s.evidence]
    for c in res.watchlist:
        rows += [[c.symbol, "WATCH", e.rule, str(e.value), str(e.threshold), bool(e.passed), e.note] for e in c.evidence]
    _table(ws, headers, rows)
    if rows:
        rng = f"F2:F{len(rows) + 1}"
        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=["TRUE"], fill=GREEN))
        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=["FALSE"], fill=RED))


def _quality(wb: Workbook, res: RunResult) -> None:
    ws = wb.create_sheet("Data Quality")
    headers = ["Symbol", "Status", "Last Bar", "Bars", "Issues"]
    order = {"FAIL": 0, "WARN": 1, "PASS": 2}
    rows = [[q.symbol, q.status, q.last_date, q.bars, "; ".join(q.issues)]
            for q in sorted(res.quality, key=lambda q: (order.get(q.status, 3), q.symbol))]
    _table(ws, headers, rows, max_width=80)
    for i, r in enumerate(rows, 2):
        ws[f"B{i}"].fill = {"FAIL": RED, "PASS": GREEN}.get(r[1], PatternFill("solid", fgColor="FFEB9C"))


def write_excel(res: RunResult, path: Path) -> Path:
    wb = Workbook()
    _summary(wb, res)
    _setups(wb, res)
    _watchlist(wb, res)
    _breakdown(wb, res)
    _checklist(wb, res)
    _dataframe(wb, "Full Scan", res.full_scan, ["technical_score", "momentum_score", "rs_percentile",
                                                "confluence_score", "final_score"])
    fs_ws = wb["Full Scan"]
    if res.full_scan is not None and not res.full_scan.empty:
        _fill_actions(fs_ws, [str(c) for c in res.full_scan.columns], len(res.full_scan), "action")
    _dataframe(wb, "Sectors", res.sectors, ["sector_score"])
    _evidence(wb, res)
    _quality(wb, res)
    wb.save(path)
    return path

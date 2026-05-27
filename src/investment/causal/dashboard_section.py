"""Causal impact chain dashboard section renderer.

Provides HTML fragments for the "影响链异动" section in DASHBOARD.html.
All data comes from the ``chain_assessments`` table.
"""
from __future__ import annotations

import json
from datetime import date as dt_date
from pathlib import Path

from investment.core.db import connect
from investment.core.settings import DB_PATH


def load_causal_assessments(conn, date: str) -> list[dict]:
    """Load L3+ chain_assessments for the given date.

    Returns list of dicts with keys suitable for HTML rendering.
    """
    rows = conn.execute(
        """SELECT ca.holding_code, ca.impact_score, ca.impact_level,
                  ca.direction, ca.paths_json, ca.triggering_signal_ids,
                  ca.narrative_md,
                  i.name AS holding_name
           FROM chain_assessments ca
           LEFT JOIN instruments i ON i.code = ca.holding_code AND i.active = 1
           WHERE ca.date = ? AND ca.impact_level IN ('L3','L4','L5')
           ORDER BY ABS(ca.impact_score) DESC""",
        (date,),
    ).fetchall()

    results = []
    for r in rows:
        paths = json.loads(r["paths_json"] or "[]")
        signal_ids = json.loads(r["triggering_signal_ids"] or "[]")

        key_nodes = []
        for p in paths[:3]:
            seq = p.get("node_sequence", [])
            for node_name in seq:
                if node_name not in key_nodes:
                    key_nodes.append(node_name)

        results.append({
            "code": r["holding_code"],
            "name": r["holding_name"] or r["holding_code"],
            "impact_score": r["impact_score"],
            "impact_level": r["impact_level"],
            "direction": r["direction"],
            "key_nodes": key_nodes[:3],
            "signal_count": len(signal_ids),
            "narrative_md": r["narrative_md"] or "",
            "paths_json": r["paths_json"],
        })

    return results


def render_causal_section(assessments: list[dict]) -> str:
    """Render the "影响链异动" HTML section.

    Returns an HTML string compatible with the dashboard's CSS classes.
    """
    if not assessments:
        return (
            '<div class="section" id="section-causal">'
            '<div class="section-title"><span class="icon">🔗</span> 影响链异动</div>'
            '<div class="alert-box alert-info">'
            '<span class="alert-icon">✅</span>'
            '<div>今日无活跃影响链</div>'
            '</div></div>'
        )

    rows = []
    for i, a in enumerate(assessments):
        level = a["impact_level"]
        direction = a["direction"]

        # Level badge styling
        level_colors = {
            "L5": ("#c53030", "#fff5f5"),
            "L4": ("#d69e2e", "#fffbeb"),
            "L3": ("#2b6cb0", "#ebf8ff"),
        }
        lc = level_colors.get(level, ("#718096", "#f7fafc"))

        # Direction icon
        dir_icon = {"positive": "📈", "negative": "📉", "neutral": "➡️"}.get(direction, "➡️")

        # Key nodes chain
        nodes_chain = " → ".join(a["key_nodes"][:3]) if a["key_nodes"] else "—"

        # Suggested action from narrative
        suggested = ""
        narrative = a["narrative_md"]
        if "建议更新 thesis" in narrative:
            suggested = "📝 更新 thesis"
        elif "建议加入复盘清单" in narrative:
            suggested = "🔍 加入复盘"
        else:
            suggested = "👁 观察"

        narrative_escaped = narrative.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

        rows.append(
            f'<tr class="causal-row" onclick="toggleCausal({i})" style="cursor:pointer">'
            f'<td>{a["code"]}</td>'
            f'<td>{a["name"]}</td>'
            f'<td><span style="display:inline-block;padding:2px 10px;border-radius:10px;'
            f'font-size:11px;font-weight:700;color:{lc[0]};background:{lc[1]}">{level}</span></td>'
            f'<td>{dir_icon} {direction}</td>'
            f'<td style="font-size:12px;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{nodes_chain}</td>'
            f'<td style="text-align:center">{a["signal_count"]}</td>'
            f'<td style="font-size:12px">{suggested}</td>'
            f'</tr>'
            f'<tr class="causal-detail" id="causal-detail-{i}" style="display:none">'
            f'<td colspan="7" style="padding:12px 16px;background:#f7f9fc;font-size:12px;'
            f'color:#4a5568;white-space:pre-wrap;max-width:800px">{narrative_escaped}</td>'
            f'</tr>'
        )

    header = (
        '<table class="data-table"><thead><tr>'
        '<th>代码</th><th>名称</th><th>等级</th><th>方向</th>'
        '<th>关键传导链</th><th>信号</th><th>建议</th>'
        '</tr></thead><tbody>'
    )

    return (
        '<div class="section" id="section-causal">'
        '<div class="section-title"><span class="icon">🔗</span> 影响链异动</div>'
        + header + "".join(rows) + "</tbody></table>"
        "</div>"
    )


def _causal_js() -> str:
    """JavaScript for expandable causal detail rows."""
    return """
function toggleCausal(idx){var d=document.getElementById('causal-detail-'+idx);if(d.style.display==='none'||!d.style.display){d.style.display='table-row'}else{d.style.display='none'}}
"""

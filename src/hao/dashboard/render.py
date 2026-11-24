"""Render dashboard metrics as HTML (for /dashboard) or Markdown (for pilot readouts)."""

from __future__ import annotations

from html import escape

from hao.platform.console import _page, _pill


def _pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def _met(m) -> str:
    return "n/a" if m is None else ("✅" if m else "❌")


def html(d: dict) -> str:
    v, q, o, g = d["volume"], d["quality"], d["outcomes"], d["governance"]
    n = max(v["tickets"], 1)
    bars = "".join(
        f"<tr><td>{_pill(lvl)}</td><td style='width:70%'><div style='background:#c7d2fe;height:14px;"
        f"width:{100 * k / n:.1f}%'></div></td><td>{k} ({100 * k / n:.0f}%)</td></tr>"
        for lvl, k in v["by_level"].items())
    targets = "".join(f"<tr><td>{escape(t['metric'])}</td><td>{escape(t['target'])}</td><td>{_pct(t['actual'])}</td>"
                      f"<td>{_met(t['met'])}</td></tr>" for t in d["targets"])
    def note(c):
        return ("demoted " if c["demoted"] else "") + (f"proposal #{c['proposal']}" if c["proposal"] else "")

    cats = "".join(
        f"<tr><td>{escape(c['category'])}</td><td>{_pill(c['policy_level'])}</td><td>{_pill(c['effective_level'])}"
        f"</td><td>{c['items']}</td><td>{_pct(c['precision'])}</td><td>{escape(c['owner'])}</td>"
        f"<td>{note(c)}</td></tr>" for c in d["categories"])
    ve = d["value_estimate"]
    ks = (f"<p class='err'><b>Kill switch is ON</b>: {escape(g['kill_switch']['reason'])}</p>"
          if g.get("kill_switch") else "")
    body = f"""
<h1>Kaveri: Human+AI operating model</h1>
{ks}
<p>Last {d['window_days']} days · policy {escape(d['policy'])} ({escape(d['policy_version'])}) ·
audit chain {'OK' if g['audit_chain_ok'] else 'BROKEN'} ({g['audit_events']} events)</p>
<div class='grid'>
<div><h2>Pilot targets</h2><table><tr><th>Metric</th><th>Target</th><th>Actual</th><th></th></tr>{targets}</table>
<h2>Human hours redirected (estimate)</h2><p style='font-size:28px;margin:4px 0'>{ve['human_hours_redirected']} h</p>
<p>{ve['minutes_saved_ai_resolved']:.0f} min saved on AI-resolved tickets + {ve['minutes_saved_approved_drafts']:.0f}
min on approved drafts − {ve['minutes_spent_on_qa']:.0f} min QA − rejected-draft review time. Assumptions:
<code>policy/value.yaml</code>.</p></div>
<div><h2>{v['tickets']} tickets by autonomy level</h2><table>{bars}</table>
<h2>Outcomes</h2><table>
<tr><td>Resolved by AI (A3)</td><td>{o['ai_resolved']}</td></tr>
<tr><td>Drafted by AI, approved by a human (A2)</td><td>{o['approved_and_resolved']}</td></tr>
<tr><td>Waiting for approval</td><td>{o['awaiting_approval']}</td></tr>
<tr><td>Drafts expired unreviewed (went to a human)</td><td>{o['expired_unreviewed']}</td></tr>
<tr><td>Handed off with a context packet (A1 / fallback)</td><td>{o['handed_off'] + o['failed_fallback']}</td></tr>
<tr><td>Human only (A0)</td><td>{o['human_only']}</td></tr>
<tr><td>Re-routed to the right group by AI</td><td>{o['rerouted_by_ai']}</td></tr>
<tr><td>Draft reviews: approved / edited / rejected</td><td>{q['approved']} / {q['edited']} / {q['rejected']}
 (override rate {_pct(q['override_rate'])})</td></tr>
<tr><td>QA samples: pass / fail</td><td>{q['qa_pass']} / {q['qa_fail']}</td></tr>
<tr><td>Reopened after AI resolution</td><td>{q['reopened']} ({_pct(q['reopen_rate_ai_resolved'])})</td></tr>
</table></div></div>
<h2>Categories: autonomy is earned</h2>
<table><tr><th>Category</th><th>Policy</th><th>Effective</th><th>Evidence</th><th>Precision</th><th>Owner</th>
<th>Controller</th></tr>{cats}</table>"""
    return _page("Leadership dashboard", body)


def markdown(d: dict) -> str:
    v, q, o, ve, g = d["volume"], d["quality"], d["outcomes"], d["value_estimate"], d["governance"]
    n = max(v["tickets"], 1)
    lines = [
        "| Metric | Target | Actual | |", "|---|---|---|---|",
        *[f"| {t['metric']} | {t['target']} | {_pct(t['actual'])} | {_met(t['met'])} |" for t in d["targets"]],
        "",
        f"**{v['tickets']} tickets**: " + " · ".join(f"{lvl} {100 * k / n:.0f}%" for lvl, k in v["by_level"].items()),
        "",
        "| Outcome | Count |", "|---|---|",
        f"| Resolved by AI acting alone (A3) | {o['ai_resolved']} |",
        f"| Drafted by AI, approved by a human (A2) | {o['approved_and_resolved']} |",
        f"| Drafts rejected | {o['rejected']} |",
        f"| Handed off with a context packet | {o['handed_off'] + o['failed_fallback']} |",
        f"| Human only (A0) | {o['human_only']} |",
        f"| Re-routed by AI | {o['rerouted_by_ai']} |",
        f"| Reopened after AI resolution | {q['reopened']} ({_pct(q['reopen_rate_ai_resolved'])}) |",
        f"| QA samples pass / fail | {q['qa_pass']} / {q['qa_fail']} |",
        f"| Override rate on drafts (edited + rejected) | {_pct(q['override_rate'])} |",
        "",
        f"**Human hours redirected (estimate): {ve['human_hours_redirected']} h**, "
        f"from {ve['minutes_saved_ai_resolved']:.0f} min on AI-resolved tickets and "
        f"{ve['minutes_saved_approved_drafts']:.0f} min on approved drafts, less {ve['minutes_spent_on_qa']:.0f} min "
        "of QA and the review time spent on rejected drafts. Assumptions: `policy/value.yaml`.",
        "",
        f"Audit chain: {'OK' if g['audit_chain_ok'] else 'BROKEN'}, {g['audit_events']} events.",
    ]
    return "\n".join(lines)

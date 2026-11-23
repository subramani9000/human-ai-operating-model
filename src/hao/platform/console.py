"""Review console: a deliberately plain, server-rendered HTML page (ADR 0009).

No front-end build, no JavaScript framework, nothing for the client's IT team to operate. One page lists
what is waiting; one page shows a draft next to the original ticket, the policy decision and the
evidence, with approve / edit / reject.
"""

from __future__ import annotations

from html import escape

from hao.db.models import HaoDecision, HaoWorkItem
from hao.intake.redaction import Vault

CSS = """
body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;
color:#1f2328;line-height:1.5}
h1{font-size:22px;font-weight:600} h2{font-size:16px;font-weight:600;margin-top:28px}
table{border-collapse:collapse;width:100%;font-size:14px} td,th{border-bottom:1px solid #e5e7eb;padding:6px 8px;
text-align:left;vertical-align:top} th{color:#57606a;font-weight:500}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;background:#eef2ff;color:#3730a3}
.A0{background:#fee2e2;color:#991b1b}.A1{background:#fef3c7;color:#92400e}.A2{background:#e0f2fe;color:#075985}
.A3{background:#dcfce7;color:#166534} .grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.box{border:1px solid #e5e7eb;border-radius:8px;padding:12px 14px;font-size:14px;white-space:pre-wrap}
.err{background:#fee2e2;color:#991b1b;padding:8px 12px;border-radius:6px}
textarea{width:100%;min-height:130px;font:inherit} input,select{font:inherit;padding:4px}
button{font:inherit;padding:6px 14px;border-radius:6px;border:1px solid #d0d7de;background:#f6f8fa;cursor:pointer}
button.primary{background:#1f883d;color:#fff;border-color:#1f883d} a{color:#0969da}
"""


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{escape(title)}</title>"
            f"<style>{CSS}</style></head><body>{body}</body></html>")


def _pill(level: str) -> str:
    return f"<span class='pill {escape(level)}'>{escape(level)}</span>"


def index(pending: list[HaoWorkItem], recent: list[HaoWorkItem]) -> str:
    def rows(items, link=True):
        out = []
        for i in items:
            num = f"<a href='/console/items/{i.id}'>{escape(i.number)}</a>" if link else escape(i.number)
            out.append(f"<tr><td>{num}</td><td>{_pill(i.level)}</td><td>{escape(i.lane)}</td>"
                       f"<td>{escape(i.category)}</td><td>{escape(i.state)}</td>"
                       f"<td>{escape(', '.join(p['tool'] for p in i.plan))}</td></tr>")
        return "".join(out) or "<tr><td colspan=6>Nothing here.</td></tr>"

    head = "<tr><th>Ticket</th><th>Level</th><th>Lane</th><th>Category</th><th>State</th><th>Plan</th></tr>"
    return _page("Review console", f"""
<h1>Kaveri: Human+AI review console</h1>
<h2>Waiting for you ({len(pending)})</h2><table>{head}{rows(pending)}</table>
<h2>Recent</h2><table>{head}{rows(recent)}</table>""")


def item(i: HaoWorkItem, d: HaoDecision | None, incident: dict, vault: Vault, reason_codes: list[str],
         error: str = "") -> str:
    draft = i.draft or {}
    reply = vault.reveal(draft.get("reply", ""))
    ctx = i.context or {}
    kb = "".join(f"<li>{escape(k['number'])}: {escape(k['title'])}</li>" for k in ctx.get("kb", [])) or "<li>none</li>"
    similar = "".join(f"<li>{escape(s['number'])} ({escape(s['category'])}): {escape(s['subject'])}</li>"
                      for s in ctx.get("similar", [])) or "<li>none</li>"
    plan = "".join(f"<li><code>{escape(p['tool'])}</code> {escape(str(p.get('args') or ''))}</li>"
                   for p in i.plan) or "<li>no actions</li>"
    steps = "".join(f"<li>{escape(s['reason'])}: {escape(s['from'])} → {escape(s['to'])}</li>"
                    for s in (d.steps if d else []) if s["from"] != s["to"]) or "<li>category default</li>"
    reasons = "".join(f"<option value='{escape(r)}'>{escape(r)}</option>" for r in reason_codes)
    qa = i.state == "qa_pending"
    verdicts = ("<option value='pass'>Pass: the AI did the right thing</option>"
                "<option value='fail'>Fail: the AI got it wrong</option>") if qa else (
                "<option value='approve'>Approve as drafted</option><option value='edit'>Approve with my edits</option>"
                "<option value='reject'>Reject: a human will handle it</option>")
    reply_box = "" if qa else (f"<p><label>Reply to the user (edit if needed)<br>"
                               f"<textarea name='reply'>{escape(reply)}</textarea></label></p>")
    form = "" if i.state not in ("awaiting_approval", "qa_pending") else f"""
<h2>{'QA review' if qa else 'Your decision'}</h2>
<form method='post' action='/console/items/{i.id}'>
 <p><label>Your email <input name='reviewer' type='email' required placeholder='you@kaveri.example'></label></p>
 {reply_box}
 <p><label>Verdict <select name='verdict'>{verdicts}</select></label>
    <label>Reason (required for edit / reject / fail)
    <select name='reason_code'><option value=''>n/a</option>{reasons}</select></label></p>
 <p><label>Note <input name='note' size='60'></label></p>
 <button class='primary' type='submit'>Submit</button>
</form>"""
    return _page(f"{i.number} review", f"""
<p><a href='/console'>← back</a></p>
<h1>{escape(i.number)} {_pill(i.level)} <span class='pill'>{escape(i.state)}</span></h1>
{f"<p class='err'>{escape(error)}</p>" if error else ""}
<div class='grid'>
 <div><h2>Original ticket</h2><div class='box'><b>{escape(incident.get('short_description', ''))}</b>
{escape(incident.get('description', ''))}</div>
 <p>Caller: {escape(incident.get('caller_id', ''))} · Priority {escape(incident.get('priority', ''))} ·
 Group {escape(incident.get('assignment_group', ''))}</p></div>
 <div><h2>Why this level</h2><p>{escape(d.explanation if d else '')}</p><ul>{steps}</ul>
 <p>Classifier: {escape(d.predicted_category if d else '')} ({(d.confidence if d else 0):.2f}) ·
 policy {escape(d.policy_version if d else '')}</p>
 <h2>Planned actions</h2><ul>{plan}</ul>
 <h2>Evidence</h2><b>KB</b><ul>{kb}</ul><b>Similar tickets</b><ul>{similar}</ul></div>
</div>
<h2>AI draft</h2><div class='box'>{escape(reply) or '<i>(no reply drafted)</i>'}</div>
<p><b>Close notes:</b> {escape(vault.reveal(draft.get('close_notes', '')))}</p>
<p><b>Handoff summary:</b> {escape(vault.reveal(draft.get('handoff_summary', '')))}</p>
{"<h2>Results</h2><div class='box'>" + escape(str(i.results)) + "</div>" if i.results else ""}
{form}""")

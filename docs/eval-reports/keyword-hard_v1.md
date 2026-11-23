# Classification eval: `keyword-v1`

Golden set: `hard_v1` · 45 tickets · input is **redacted** text, exactly as in production.

| Metric | Value |
|---|---|
| Accuracy | **26.7%** |
| Macro F1 | 0.303 |
| Application accuracy | 57.8% |
| Returned `unknown` | 44.4% |
| **Risk-downgrade errors** (true risk tier > predicted) | **5** |
| Expected calibration error | 0.205 |
| Latency mean / p95 | 0 ms / 0 ms |
| Tokens | 0 |
| Cost | not tracked (set LLM_*_PRICE_PER_MTOK) |
| API errors | 0 |

## Accuracy by phrasing style

| Style | Accuracy |
|---|---|
| handwritten | 26.7% |

## Per category

| Category | Precision | Recall | F1 | n |
|---|---|---|---|---|
| account_unlock | 0.00 | 0.00 | 0.00 | 2 |
| app_access_request | 0.67 | 0.67 | 0.67 | 3 |
| app_error | 0.33 | 0.17 | 0.22 | 6 |
| email_issue | 0.50 | 0.50 | 0.50 | 2 |
| expense_claim_issue | 0.00 | 0.00 | 0.00 | 2 |
| how_to_question | 1.00 | 0.33 | 0.50 | 3 |
| laptop_hardware | 0.67 | 0.50 | 0.57 | 4 |
| network_outage | 0.00 | 0.00 | 0.00 | 2 |
| password_reset | 0.20 | 0.25 | 0.22 | 4 |
| payroll_access | 1.00 | 0.67 | 0.80 | 3 |
| payroll_discrepancy | 0.33 | 0.50 | 0.40 | 2 |
| printer_issue | 1.00 | 0.50 | 0.67 | 2 |
| security_phishing | 0.00 | 0.00 | 0.00 | 4 |
| software_install | 0.00 | 0.00 | 0.00 | 2 |
| vpn_access | 0.00 | 0.00 | 0.00 | 4 |

## Calibration

| Confidence | n | Mean confidence | Actual accuracy |
|---|---|---|---|
| 0.0–0.5 | 22 | 0.04 | 0.0% |
| 0.5–0.7 | 4 | 0.59 | 50.0% |
| 0.9–1.0 | 19 | 0.95 | 52.6% |

## Top confusions

| True | Predicted | Count |
|---|---|---|
| app_error | unknown | 4 |
| account_unlock | password_reset | 2 |
| how_to_question | unknown | 2 |
| vpn_access | unknown | 2 |
| software_install | unknown | 2 |
| security_phishing | unknown | 2 |
| password_reset | laptop_hardware | 1 |
| password_reset | unknown | 1 |

## Sample errors

| Ticket | Style | True | Predicted | Conf. | Subject |
|---|---|---|---|---|---|
| HRD0001 | handwritten | password_reset | laptop_hardware | 0.95 | Can't get into anything since Monday |
| HRD0002 | handwritten | password_reset | unknown | 0.00 | KLMS credentials |
| HRD0003 | handwritten | password_reset | payroll_discrepancy | 0.54 | PeopleDesk login |
| HRD0004 | handwritten | account_unlock | password_reset | 0.95 | too many attempts |
| HRD0005 | handwritten | account_unlock | password_reset | 0.40 | WMS-Pro handheld rejecting me |
| HRD0006 | handwritten | how_to_question | unknown | 0.00 | Teams meeting recording |
| HRD0007 | handwritten | how_to_question | unknown | 0.00 | KLMS bulk upload |
| HRD0009 | handwritten | vpn_access | app_access_request | 0.95 | remote working from Singapore |
| HRD0010 | handwritten | vpn_access | unknown | 0.00 | cannot reach intranet from home |
| HRD0011 | handwritten | vpn_access | password_reset | 0.95 | GlobalVPN cert expired |
| HRD0012 | handwritten | email_issue | unknown | 0.00 | calendar invites not arriving |
| HRD0014 | handwritten | software_install | unknown | 0.00 | need Power BI |
| HRD0015 | handwritten | software_install | unknown | 0.00 | request: VS Code |
| HRD0017 | handwritten | printer_issue | unknown | 0.00 | scan to email |
| HRD0018 | handwritten | laptop_hardware | unknown | 0.00 | screen flickering |

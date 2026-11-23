# Classification eval: `keyword-v1`

Golden set: `golden_v1` · 300 tickets · input is **redacted** text, exactly as in production.

| Metric | Value |
|---|---|
| Accuracy | **94.7%** |
| Macro F1 | 0.947 |
| Application accuracy | 91.7% |
| Returned `unknown` | 0.3% |
| **Risk-downgrade errors** (true risk tier > predicted) | **14** |
| Expected calibration error | 0.175 |
| Latency mean / p95 | 0 ms / 0 ms |
| Tokens | 0 |
| Cost | not tracked (set LLM_*_PRICE_PER_MTOK) |
| API errors | 0 |

## Accuracy by phrasing style

| Style | Accuracy |
|---|---|
| angry | 100.0% |
| formal | 93.8% |
| hinglish | 94.1% |
| tanglish | 94.8% |
| terse | 93.4% |

## Per category

| Category | Precision | Recall | F1 | n |
|---|---|---|---|---|
| account_unlock | 0.83 | 1.00 | 0.91 | 20 |
| app_access_request | 0.90 | 0.95 | 0.93 | 20 |
| app_error | 0.93 | 0.65 | 0.76 | 20 |
| email_issue | 0.83 | 1.00 | 0.91 | 20 |
| expense_claim_issue | 1.00 | 1.00 | 1.00 | 20 |
| how_to_question | 1.00 | 0.95 | 0.97 | 20 |
| laptop_hardware | 0.95 | 1.00 | 0.98 | 20 |
| network_outage | 1.00 | 0.95 | 0.97 | 20 |
| password_reset | 0.87 | 1.00 | 0.93 | 20 |
| payroll_access | 1.00 | 0.85 | 0.92 | 20 |
| payroll_discrepancy | 1.00 | 1.00 | 1.00 | 20 |
| printer_issue | 1.00 | 1.00 | 1.00 | 20 |
| security_phishing | 1.00 | 0.90 | 0.95 | 20 |
| software_install | 1.00 | 0.95 | 0.97 | 20 |
| vpn_access | 1.00 | 1.00 | 1.00 | 20 |

## Calibration

| Confidence | n | Mean confidence | Actual accuracy |
|---|---|---|---|
| 0.0–0.5 | 19 | 0.38 | 63.2% |
| 0.5–0.7 | 89 | 0.62 | 95.5% |
| 0.7–0.8 | 61 | 0.74 | 100.0% |
| 0.8–0.9 | 4 | 0.83 | 100.0% |
| 0.9–1.0 | 127 | 0.95 | 96.1% |

## Top confusions

| True | Predicted | Count |
|---|---|---|
| app_error | account_unlock | 4 |
| security_phishing | password_reset | 2 |
| app_error | email_issue | 2 |
| payroll_access | app_access_request | 2 |
| app_access_request | email_issue | 1 |
| network_outage | app_error | 1 |
| how_to_question | unknown | 1 |
| app_error | password_reset | 1 |

## Sample errors

| Ticket | Style | True | Predicted | Conf. | Subject |
|---|---|---|---|---|---|
| INC0010174 | terse | security_phishing | password_reset | 0.95 | phising? |
| INC0010439 | hinglish | app_access_request | email_issue | 0.40 | KLMS access chahiye |
| INC0010541 | formal | network_outage | app_error | 0.95 | Network fown - umbai warehouse |
| INC0010831 | formal | app_error | account_unlock | 0.58 | RouteOptima error: Route plan job timed out after 300s |
| INC0011150 | formal | app_error | account_unlock | 0.58 | klms error: ora-01017: invalid username/password |
| INC0011777 | terse | security_phishing | password_reset | 0.40 | phisihng? |
| INC0011815 | terse | how_to_question | unknown | 0.00 | Teams question |
| INC0012372 | hinglish | app_error | email_issue | 0.40 | klms mein error |
| INC0012409 | tanglish | payroll_access | app_access_request | 0.95 | payroll access venum |
| INC0012781 | hinglish | app_error | email_issue | 0.40 | CustomsLink mein error |
| INC0013721 | formal | app_error | account_unlock | 0.58 | customslink error: session expired while filing |
| INC0013938 | terse | app_error | password_reset | 0.40 | KLMS not working |
| INC0014219 | formal | app_error | account_unlock | 0.58 | KLMS error: ORA-01017: invalid username/password |
| INC0014247 | tanglish | software_install | laptop_hardware | 0.95 | 7-Zip insylal pannanum |
| INC0014650 | tanglish | payroll_access | app_access_request | 0.95 | payroll access venum |

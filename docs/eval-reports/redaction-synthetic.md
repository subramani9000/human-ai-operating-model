# Redaction eval

5000 tickets · 8714 PII values present verbatim in text · 6971 redacted spans

| PII type | Present | Leaked | Recall |
|---|---|---|---|
| email_address | 402 | 0 | 100.00% |
| employee_id | 2547 | 0 | 100.00% |
| person_name | 2818 | 0 | 100.00% |
| personal_email | 334 | 0 | 100.00% |
| phone_number | 2613 | 0 | 100.00% |
| ALL | 8714 | 0 | 100.00% |

- **Precision** (redacted spans that were real PII, typo-tolerant): 100.00%
- **Utility** (application name still readable after redaction): 100.00%


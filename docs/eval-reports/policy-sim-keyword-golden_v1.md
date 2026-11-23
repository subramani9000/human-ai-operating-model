# Policy simulation: `keyword` on `golden_v1`

Each ticket is decided twice: with the classifier's output (what production would do) and with the true label at full confidence (what the policy intends). **Over-autonomy** = AI got more autonomy than intended (dangerous). **Under-autonomy** = less than intended (safe, costs automation).

| Scenario | Levels as classified | Over-autonomy | …on high/critical work | Under-autonomy | Exact |
|---|---|---|---|---|---|
| current policy | A3 0% · A2 9% · A1 45% · A0 46% | **1** | **1** | 129 | 57% |
| current policy, rules off | A3 0% · A2 22% · A1 65% · A0 13% | **8** | **3** | 1 | 97% |
| promote password_reset→A3, account_unlock→A3, how_to_question→A3 | A3 4% · A2 10% · A1 40% · A0 46% | **1** | **1** | 129 | 57% |
| promote password_reset→A3, account_unlock→A3, how_to_question→A3, rules off | A3 10% · A2 12% · A1 65% · A0 13% | **8** | **3** | 1 | 97% |

Intended levels (oracle), first scenario: A3 0% · A2 20% · A1 67% · A0 13%

## Over-autonomy examples: current policy

| Ticket | True | Predicted | Conf. | Intended | Got | High risk |
|---|---|---|---|---|---|---|
| INC0010541 | network_outage | app_error | 0.95 | A0 | A1 | yes |

## Over-autonomy examples: current policy, rules off

| Ticket | True | Predicted | Conf. | Intended | Got | High risk |
|---|---|---|---|---|---|---|
| INC0010174 | security_phishing | password_reset | 0.95 | A0 | A2 | yes |
| INC0010541 | network_outage | app_error | 0.95 | A0 | A1 | yes |
| INC0010831 | app_error | account_unlock | 0.58 | A1 | A2 |  |
| INC0011150 | app_error | account_unlock | 0.58 | A1 | A2 |  |
| INC0011777 | security_phishing | password_reset | 0.40 | A0 | A2 | yes |
| INC0013721 | app_error | account_unlock | 0.58 | A1 | A2 |  |
| INC0013938 | app_error | password_reset | 0.40 | A1 | A2 |  |
| INC0014219 | app_error | account_unlock | 0.58 | A1 | A2 |  |

## Over-autonomy examples: promote password_reset→A3, account_unlock→A3, how_to_question→A3

| Ticket | True | Predicted | Conf. | Intended | Got | High risk |
|---|---|---|---|---|---|---|
| INC0010541 | network_outage | app_error | 0.95 | A0 | A1 | yes |

## Over-autonomy examples: promote password_reset→A3, account_unlock→A3, how_to_question→A3, rules off

| Ticket | True | Predicted | Conf. | Intended | Got | High risk |
|---|---|---|---|---|---|---|
| INC0010174 | security_phishing | password_reset | 0.95 | A0 | A3 | yes |
| INC0010541 | network_outage | app_error | 0.95 | A0 | A1 | yes |
| INC0010831 | app_error | account_unlock | 0.58 | A1 | A2 |  |
| INC0011150 | app_error | account_unlock | 0.58 | A1 | A2 |  |
| INC0011777 | security_phishing | password_reset | 0.40 | A0 | A3 | yes |
| INC0013721 | app_error | account_unlock | 0.58 | A1 | A2 |  |
| INC0013938 | app_error | password_reset | 0.40 | A1 | A2 |  |
| INC0014219 | app_error | account_unlock | 0.58 | A1 | A2 |  |

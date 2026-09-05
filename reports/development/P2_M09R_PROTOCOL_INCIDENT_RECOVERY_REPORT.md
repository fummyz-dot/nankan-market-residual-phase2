# P2-M09R — Protocol Incident Recovery & Outer-Validation Integrity Audit

## STATUS
`AUTHORIZED_TO_RESUME_P2_M09_UNCHANGED`

## Incident
`P2-INC-001` is permanently retained as an unregistered March-to-April inner-validation two-tree probe. It is excluded from formal M09 configuration selection and is not a data-leakage finding.

## Outer-validation integrity
No M09 formal-output or checkpoint artifact exists. May (WF1), June (WF2), and July (WF3) outer-validation candidate loss, Market delta, prediction, selected configuration, and performance-driven feature-importance artifacts were not produced.

## Frozen protocol and adaptation
M08B backend config, six-config grid, walk-forward dates, FS00 list, and objective-adapter hashes reconcile. M09-specific zero-tree implementation preceded the incident and did not change frozen model mathematics. The post-incident code change is only the explicit formal-execution guard; it cannot run real-data M09 without `P2_FORMAL_M09_EVALUATION=1`.

## Accounting and resumption
Formal H1 search budget remains `0/6`; the incident is separately counted as one incidental performance peek. M09 may resume unchanged. Its historical evidence must be labelled `HISTORICAL_MARKET_TIME_UNKNOWN`, `DEVELOPMENT_REFERENCE_ONLY`, and `DEVELOPMENT_EVALUATION_WITH_RECORDED_PROTOCOL_INCIDENT`.

## M09R exclusions
This audit did not train LightGBM, compute any loss, inspect outer-validation performance, run a config, bootstrap, or compute feature importance.

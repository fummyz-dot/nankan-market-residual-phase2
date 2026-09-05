# P2-A00 Setup Preflight Report

## 1. Executive status

`READY_FOR_P2_A01`

## 2. Workspace root

`/home/nabe/projects/nankan-market-residual-phase2`

## 3. Files/directories created

### Active directories

- `audit/setup`
- `data/curated`
- `data/feature_store`
- `data/manifests`
- `data/raw/current_info`
- `data/raw/keibabook`
- `data/raw/market_snapshots`
- `data/staging`

### Governance, implementation, and audit outputs

- `docs/WORKSPACE_POLICY.md`, `docs/PROJECT_STATE.md`, `docs/DECISIONS.md`
- `.agent/PLANS/P2-A00_setup_preflight.md`
- `src/audit/p2_a00_setup_preflight.py`, `tests/unit/test_p2_a00_setup_preflight.py`
- SHA-256 manifests, setup audit artifacts, and this report

## 4. V1 reference inventory

- Manifest entries: 162
- Tool parity: 36 source / 36 reference

## 5. DB integrity

- `nankan_history.sqlite`: quick_check=ok; tables=4; logical=MATCH
- `nankan_market.sqlite`: quick_check=ok; tables=9; logical=MATCH

## 6. Raw NAR archive coverage

- Race ZIPs: 79 (expected 79)
- Odds ZIPs: 5 (expected 5)

## 7. V1 tools parity

- All source `.py` tools are present: `True`

## 8. V1 docs/contracts

- Required contracts present: `True`

## 9. Keibabook sample status

- training_summary: `PASS`
- ability_summary: `PASS`
- excluded_fields: `PASS`

## 10. Gitless provenance changes

- `vcs_mode: none` and SHA-256 manifests are the active provenance contract; Git initialization was not performed.

## 11. Missing required items

- None.

## 12. Optional missing items

- `reference/v1/audit/job06`
- `reference/v1/audit/job07`
- `reference/v1/audit/job08`
- `reference/v1/audit/job09`
- `reference/v1/audit/job1c`
- `reference/v1/audit/job1d`
- `reference/v1/audit/job1e`
- `reference/v1/audit/job2a`
- `reference/v1/audit/job2b1`
- `reference/v1/audit/job2b2a`
- `reference/v1/audit/job2b2b`
- `reference/v1/audit/job3a`
- `reference/v1/audit/job3b1a`
- `reference/v1/audit/job3b2a`
- `reference/v1/audit/job3b2b`
- `reference/v1/audit/job4a`
- `reference/v1/audit/job4b1`
- `reference/v1/audit/job4b2a`
- `reference/v1/audit/job4b2b`

## 13. Immutability status

- Locked after manifest/report generation; see `reference_permission_audit.csv`.

## 14. Known limitations

- No confirmed historical actual pre-race snapshot collector exists; `odds_snapshots = 0` is expected and not repaired.
- `MARKET_TIME_UNKNOWN` official odds remain development references only.

## 15. P2-A01 readiness decision

`READY_FOR_P2_A01`

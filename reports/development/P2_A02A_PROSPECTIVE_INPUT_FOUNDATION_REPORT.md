# P2-A02A Prospective Input Foundation Report

## 1. Executive status

`READY_FOR_P2_A02B_LIVE_SOURCE_ADAPTER`. The Phase 2 prospective foundation is operational with synthetic local validation only. No live-source semantics were inferred.

## 2. DB/schema

`db/market_snapshot.sqlite` contains an independent v2 race registry, raw capture ledger, market snapshot table, Keibabook external registry, operational status ledger, and process-supervision tables.

## 3. URL-triggered capture design

`python -m src.ingestion.capture_url` archives exact user-submitted URL bytes and metadata. Its parser version remains `SOURCE_ADAPTER_PENDING_LIVE_SAMPLE`.

## 4. Timestamp contract

Request, capture, source-publication, and scheduled-post timestamps are separately stored as timezone-aware values. Unknown source publication time is `NULL`.

## 5. Body-weight quarantine

P2_CURRENT uses a positive allow-list. Mixed odds/prediction fields remain raw-only and do not enter the curated output.

## 6. Market snapshot design

Snapshot roles include `PRIMARY_CANDIDATE` but never `PRIMARY_FROZEN`; T-15 remains an engineering candidate. Post-primary captures are diagnostic-only.

## 7. Keibabook external capture

Ability is `P2X_O`; Training is `P2X_S`. Prohibited ability fields are excluded from sanitized external representations and neither namespace is Phase 2 Main.

## 8. Operational missingness

The documented status registry distinguishes user input absence and operational/source failures from model decisions.

## 9. Process supervision

The foundation provides persisted worker records, atomic heartbeat markers, stale-progress detection, checkpoints, terminal markers, parent failure propagation, and orphan audit. This job started no child/background process.

## 10. Tests

Unit, integration, and leakage tests use local synthetic inputs. No live network test is required or executed.

## 11. Remaining live-source unknowns

MARKET and BODY_WEIGHT adapters remain `SOURCE_ADAPTER_PENDING_LIVE_SAMPLE`. Actual historical pre-race snapshots remain unconfirmed.

## 12. A02B readiness

P2-A02B may begin after an authorized live sample is retained for each adapter; its source semantics must be reviewed without altering this foundation's quarantine and timestamp contracts.

# Phase 2 Prospective Source Contract

Each capture records source type/name/reference/submitted URL, request and capture times, raw archive path, SHA-256, byte size, collector/parser versions, and outcome/error metadata. Raw bytes are append-only; a re-fetch creates a new capture.

Source adapters are `SOURCE_ADAPTER_PENDING_LIVE_SAMPLE` until an explicit user-provided or otherwise authorized live response is archived and its source semantics are reviewed. This job implements no CSS selector, hidden API, or source-specific field inference.

The Nankan official historical adapter is a retained parser fixture only. Its raw bytes, redirect chain, and available HTTP/cache metadata are archived, while all parsed odds are `HISTORICAL_FIXTURE_ONLY`. Observed odds URL suffixes are DOM evidence, not a live URL-generation contract.

The live freshness probe is a single foreground command (`python -m src.operations.live_freshness_probe <race_entry_url>`). It uses WSL direct response bytes, `captured_at`, and SHA-256 as source of truth; browser observations are not evidence. Each successful mark is atomic-checkpointed, and missed marks are never backfilled.

Keibabook capture is parallel external collection: Ability is `P2X_O`; Training is `P2X_S`. Ability fields `RT`, `CPU予想`, `展開予想`, `単勝オッズ`, `過去走人気`, and `raw_text` are prohibited from its sanitized representation. Neither namespace is Phase 2 Main.

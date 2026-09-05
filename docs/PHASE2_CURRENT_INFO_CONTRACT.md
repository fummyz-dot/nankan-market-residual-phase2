# Phase 2 Current Information Contract

Raw current-info responses are retained unchanged. Curated `P2_CURRENT` is built by a positive allow-list only: race identity, horse number, body weight, body-weight change, scratch/cancellation status, `captured_at`, and known `published_at`.

Odds, popularity, predictions, marks, CPU predictions, payouts, results, placing, and market rank are prohibited. Keyword detection is an additional audit signal only; it is not the protection boundary. The retained live sample is now implemented under P2-M11A. This document's allow-list boundary remains in force and is incorporated by `P2_CURRENT_SOURCE_CONTRACT.md`, `P2_CURRENT_CANDIDATE_FEATURE_CONTRACT.md`, and `P2_PROSPECTIVE_STABILIZATION_CONTRACT.md`; it is not deleted or weakened.

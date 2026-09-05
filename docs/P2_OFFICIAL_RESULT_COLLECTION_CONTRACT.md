# P2 Official Result Collection Contract

Only South Kanto official Nankankeiba result pages are admissible. A result URL
is resolved from the explicitly registered official entry page; it is never
constructed from a guessed URL. Each response retains its bytes, URL, capture
time, HTTP metadata, SHA-256, and archive path.

`RESULT_OFFICIAL_FINAL` requires verified race identity, parsed runner results,
and official WIN, WIDE, and TRIO payout tables. Unknown finality, parser drift,
identity mismatch, HTTP failure, or a missing parent is safe failure and cannot
settle reconciliation.

WIN combinations are one horse number; WIDE pairs and TRIO triples are sorted
only for identity. All official payout rows are retained, including multiple
dead-heat settlement rows. Where the official page does not state payout unit,
the amount is stored with `PAYOUT_UNIT_UNRESOLVED`; profit calculation is
prohibited.

Same raw hash for the same race is an idempotent no-op. Changed raw bytes create
a new capture version; prior raw evidence is never overwritten.

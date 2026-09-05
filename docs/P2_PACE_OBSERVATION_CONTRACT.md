# P2 Pace Observation Contract

The M05A race and runner prototype datasets are post-race historical
observations, not target-race model inputs. Any M05B history build must use
only `observation.race_date < target.race_date`; same-calendar-date and current
target-race observations are prohibited.

Runner observations contain raw `last_3f`, within-race median-relative closing
advantage, and average-tie rank percentile when at least two safe runners are
available. Positive closing advantage means a faster (smaller) last-3F than the
field median. Rank percentile is 1.0 fastest and 0.0 slowest.

Race observations retain parser status and raw-derived fields. Pace balance is
`race_first_3f_seconds - race_final_3f_seconds`: positive is slow-to-fast.
Full-lap descriptors use full 200m segments only and are not a permission for
additional feature variants. Exchange observations are structured but their
M05B Main-history policy remains undecided.

# Process Supervision Policy

## Default execution
Prefer foreground, synchronous, bounded, checkpointed work. Background/parallel workers are used only when required and always through `ProcessSupervisor`.

## Running definition
`RUNNING` requires a supervisor, worker process, fresh heartbeat, and fresh progress. A PID alone is insufficient.

## Required worker record
Persist `worker_id`, `pid`, started/heartbeat/progress times, progress value, stdout/stderr paths, exit code, end time, status, and failure reason. Valid statuses are `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`, `STALE`, and `CANCELLED`.

## Failure, markers, and recovery
Workers atomically update heartbeat records. Stale heartbeat records `STALE_WORKER_DETECTED`; stale progress with a fresh heartbeat records `STALE_PROGRESS_DETECTED`. Any child failure makes the supervisor `FAILED`. `RUNNING`, `COMPLETE`, and `FAILED` markers are mutually exclusive; `COMPLETE` is written only after all tracked workers succeed. Checkpoints record the last successful partition; partial output is not promoted.

## Closeout
The supervisor collects each child exit code and performs an orphan-PID audit. Job closeout requires `orphan_processes_detected = 0`.

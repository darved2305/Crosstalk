# ADR 012: Hot-path accounting

Status: accepted.

The shaper does not emit a standard log or trace per SSE emission. It queues bounded typed emission rows for one asynchronous NDJSON writer. Queue saturation backpressures the stream. Session collection extracts rows by request ID into oracle Parquet.

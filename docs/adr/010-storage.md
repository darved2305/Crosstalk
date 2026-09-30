# ADR 010: Experimental storage

Status: accepted.

Append-only experimental tables use typed PyArrow schemas and Parquet. Run manifests record provenance and validation state. PostgreSQL is reserved for later orchestration state; it is not part of the local encrypted-traffic instrument.

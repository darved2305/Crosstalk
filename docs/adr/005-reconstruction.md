# ADR 005: Capture reconstruction

Status: accepted.

Production reconstruction uses the repository's Python pcap/pcapng, IP/TCP, bounded reassembly, and TLS record parsers. Truncated captures, conflicting TCP overlaps, unresolved holes, and TLS desynchronization invalidate affected flows. tshark is invoked only after reconstruction for comparison.

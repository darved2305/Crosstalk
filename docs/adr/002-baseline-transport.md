# ADR 002: Baseline transport

Status: accepted.

The baseline uses HTTP/1.1 over TLS 1.3, one request per connection, ALPN `http/1.1`, `TCP_NODELAY`, and no TLS session tickets. The client-facing service is the TLS endpoint. Connection reuse and multiplexing are separate future protocol arms.

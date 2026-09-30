# ADR 008: Observer and oracle isolation

Status: accepted.

Observer-visible records, packets, flow hints, and flow maps reside under `observer/`. Emission and client timing data reside under `oracle/`; plaintext and key logs reside under `restricted/`. Loader guards, import contracts, and container mounts enforce the boundary. Flow attribution uses ports and connection ordering, never plaintext.

# ADR 006: Policy selection

Status: accepted; only passthrough is implemented in the current instrument.

The fixed policy set is loaded at startup. In lab mode a fixed-width arm header selects one policy per request; production mode rejects lab headers. The selected arm and policy-set hash belong in run provenance. Additional defense policies are outside the present scope.

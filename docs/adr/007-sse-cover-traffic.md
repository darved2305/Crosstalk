# ADR 007: SSE cover traffic

Status: accepted; cover traffic is outside the present scope.

Future padding and cover traffic use SSE comment lines. Ordinary `data:` events are visible to OpenAI clients and must not be used as deployment-compatible dummies. Passthrough preserves raw upstream event bytes.

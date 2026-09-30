# ADR 003: Shaper runtime

Status: accepted, host validation pending.

Python 3.13 with asyncio and uvloop is the selected data-path runtime. Emissions no larger than 16,384 plaintext bytes are passed to one transport write each, while the ciphertext record mapping is measured independently. The short Windows probe in `bench/RESULTS.md` is exploratory; the required Linux host run with uvloop and the full duration remains pending.

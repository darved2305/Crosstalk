# ADR 001: Observation layers

Status: accepted.

Application emissions, reconstructed TLS records, and captured network packets are distinct observations. The shaper writes emission accounting into the oracle tree. The reconstruction pipeline derives TLS records and packets from ciphertext captures in the observer tree. Validation measures correspondence between these layers; analysis does not infer one layer's boundaries from another.

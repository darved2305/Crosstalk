# ADR 004: Packet capture

Status: accepted, lab validation pending.

The capture service runs dumpcap in the load generator's network namespace on the non-loopback edge interface. It disables and records receive and segmentation offloads, uses full snap length with a TCP port 8443 filter, and persists drop counters. tshark is an independent validation oracle. The capture container alone receives packet and network capabilities; the shaper receives none.

# ADR 0001: Unique delivery key

Status: accepted for this example.

Decision: durable unique delivery keys guard the delivery write. This addresses concurrent retries, which in-memory locks alone cannot prevent.

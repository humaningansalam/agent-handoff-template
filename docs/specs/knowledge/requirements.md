# Knowledge Requirements

Knowledge stores concise reusable decisions, invariants, and failure modes as direct v2 claim/reason/source records. Direct records are authoritative; projections are derived accelerators.

Default query uses a bounded direct-record fallback when a projection is missing, stale, or invalid. Projection damage is a warning; direct-record corruption remains an error. Source digest drift is reported without hiding a record. A missing source may relocate only when one bounded candidate has the recorded identity; ambiguous candidates fail closed.

The compatibility status `reviewed` means only current and not superseded. It is not a human-approval assertion. A legacy migration warning is suppressed only when one semantically matching current record already exists; differing reason or source remains distinguishable.

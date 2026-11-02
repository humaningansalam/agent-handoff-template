# Architecture overview

HTTP handlers validate reminder requests; a persistent schedule stores due times and delivery IDs; a worker calls a notification adapter. Storage constraints, not chat history, determine delivery idempotence.

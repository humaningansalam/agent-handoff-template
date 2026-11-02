# Example: Event Reminder Service — Product Requirements

## Purpose
People can schedule event reminders and receive a reliable notification without duplicates.

## Current scope
One REST entrypoint, a durable reminder schedule, and a testable notification delivery adapter.

## Non-goals
No multi-region routing, billing, or automated code-agent orchestration.

## Read map
- [Architecture](architecture/overview.md)
- [Reminder requirements](specs/reminders/requirements.md)
- [Reminder design](specs/reminders/design.md)
- [Idempotent deliveries ADR](adr/0001-idempotent-delivery.md)
- [Research](research/scheduling-tradeoffs.md)

## Success criteria
A duplicate worker retry results in exactly one recorded delivered notification; restart recovery maintains due reminders.

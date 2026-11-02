# Reminder design

The scheduler persists reminders before acknowledging the HTTP request. A unique key prevents duplicate successful delivery records. The adapter is injected so tests can force transient errors.

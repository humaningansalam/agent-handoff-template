# Reminder requirements

- Creating a reminder validates recipient, date and delivery key.
- Each delivery key has at most one successful delivery record.
- Due reminders survive process restarts.
- Tests cover validation, concurrent retries and restart recovery.

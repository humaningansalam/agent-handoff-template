# Contracts (Example)

Use this file for stable interface shapes and schemas.
Do not use it for vision, policy, or general flow.

## Data Model

List the core entities, tables, files, or records.

Suggested format:
- name
- fields
- meaning
- invariants

Example template:

### ExampleEntity
- `id`: unique identifier
- `status`: lifecycle state
- `created_at`: creation timestamp
- `updated_at`: last update timestamp

## API Contracts

Document important request/response shapes.

Suggested format:
- endpoint or action
- request shape
- response shape
- error shape

Example template:

### POST /example
- Request:
  ```json
  {
    "field": "value"
  }
  ```
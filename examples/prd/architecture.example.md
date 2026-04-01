# Architecture (Example)

Use this file for the system's high-level structure and boundaries.
Keep it architectural, not task-local.

## System Overview

Summarize the overall system in a few short paragraphs.

Prompt:
- What are the major moving parts?
- What runs where?
- What is the source of truth?
- Which parts are internal vs external?

## Main Components

List the major components and their responsibilities.

Suggested categories:
- Client / UI
- API / backend
- Worker / agent
- Database / storage
- External integrations

## Component Boundaries

Clarify ownership and non-ownership.

Prompt:
- What does each component own?
- What does it not own?
- Where do cross-component contracts live?

## Deployment Overview

Describe the major environments and deployment shape.

Examples:
- local
- staging
- production
- hosted services
- self-hosted services

## Key Design Decisions

Record important high-level decisions and why they exist.

Suggested format:
- Decision:
- Why:
- Tradeoff:
- Revisit when:

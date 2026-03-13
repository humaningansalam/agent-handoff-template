# 🔄 Agent Handoff Template

A portable, file-based shared memory baseline for coding agents (Claude Code, Cursor, Windsurf, etc.).

## 🚀 Why this template?
When working in a multi-agent environment (e.g., moving between a terminal-based agent and an editor-based agent), context loss is inevitable.

This repository provides a **lightweight shared memory structure**. By using a standardized markdown handoff protocol, you can reduce context bloat, preserve architectural decisions, and ensure a deterministic workflow across different AI tools.

## 📦 Architecture (3-Layer Concept)

**Layer 1: Core Portable Memory (Root & `docs/`)**
The source of truth. Tool-agnostic markdown files tracking state, tasks, and rules.
- `SESSION_HANDOFF.md`: Active state and verification results.
- `docs/TASK.md`: Global progress checklist.
- `docs/PRD.md`: Long-term architectural rules.
- `docs/archive/`: Cold storage for completed contexts.

**Layer 2: Thin Adapters (`examples/`)**
Tool-specific instructions that bridge your AI to the Core Memory.
- See the `examples/` folder for `.mdc` rules, `CLAUDE.md`, or `AGENTS.md` templates.

**Layer 3: Generic SOPs (`.agent/workflows/`)**
Standard Operating Procedures (SOPs) for complex or risky tasks (e.g., DB migrations). Agents read these before executing specific actions.

## 🛠️ Getting Started
1. Copy `SESSION_HANDOFF.md`, `docs/`, and `.agent/` into your project root.
2. Check the `examples/` folder for your specific AI tool. Copy the relevant adapter file to your project root (e.g., rename `cursor-rule.mdc.example` to `.cursor/rules/handoff.mdc`).
3. Agents will now automatically read the handoff files, verify their work, and update the state before exiting.

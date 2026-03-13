# 🗑️ Archive (Context Hygiene)

> **Purpose:** Keep the AI's context window clean and save tokens.

When a major feature is fully completed, or an architectural document becomes outdated, **DO NOT delete it**. Move it to this `archive/` folder.

**Why?**
1. **Token Savings:** Active agents won't read these files by default, saving context window space.
2. **Historical Context:** If an agent ever needs to know *why* a past decision was made, it can still search and retrieve files from this directory.

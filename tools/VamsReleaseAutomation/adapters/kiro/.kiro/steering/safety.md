---
inclusion: always
---

# Safety

The rules are in `#[[file:skill/SKILL.md]]` section 1; follow them as written.
Kiro specifics:

-   A Kiro tool-trust prompt is not a release approval. **[HUMAN]** steps need
    the user's explicit answer in chat.
-   The `vra-guard` preToolUse hook and the agents' denied commands are a
    second layer; the engine refuses every human-only action for an agent on
    its own.

# Claude Long-Session Improvements, parked plan

> **Status: PARKED, do not implement yet.** Captured 2026-05-21 (on the laptop). Resume when Sean greenlights, likely from the home PC.
> **Origin:** Sean observed that long-running Claude Code sessions "go off the rails the further it goes" and wants the session re-anchored periodically + the ability to feed in extra context mid-session. We agreed to **note and plan, not build, now.**

## Problem
Long-context instruction drift: as a Claude Code session grows, adherence to the original system prompt / goal weakens.

## Key insight (shapes the fix)
In Claude Code the system prompt is **re-sent on every turn**, it never falls out of context. Drift is **dilution** (its weight shrinks as the conversation grows around it; attention disperses; recency bias favours the latest turns), not eviction. So literally re-pasting the whole system prompt is low-value and burns tokens. Fix the dilution, not a non-existent eviction.

## Candidate levers
1. **Periodic targeted reminders (recommended).** A `UserPromptSubmit` hook that injects a compact "north star" each turn, current goal + the 3-4 hard constraints, rather than the full prompt. Same machinery as the `<system-reminder>` lines already in the transcript. Configured in `settings.json`; the `update-config` skill sets up hooks.
2. **Compaction discipline.** `/compact` (or auto-compact, optionally a `PreCompact` hook) to summarise + reset a bloated session *before* it drifts.
3. **Durable scratchpad.** Keep goal/state in `CLAUDE.md` or a memory file the agent re-reads, so it re-grounds from state rather than fuzzy recall. Pairs with the productivity/memory plugin (TASKS.md + memory).
4. **Sub-agents for long sub-tasks.** Offload bounded work to fresh agent contexts so the main thread stays lean.

**Pick:** (1) + (2), north-star reminder hook + `/compact` on long runs, with (3) as the backing store and (4) used opportunistically.

## Potential plan (when greenlit)
1. **Analyse a real drifting session** → pull a long PC session via Claude Code transcript search, find where adherence broke, identify which constraint(s) got dropped. *Verify:* concrete drift points named.
2. **Draft the north-star content**, goal + only the constraints that actually slipped. Keep it short. *Verify:* a few lines, covers the observed failures.
3. **Wire the `UserPromptSubmit` hook** in `settings.json` (via update-config skill); decide cadence (every turn vs every N turns / on a trigger). *Verify:* reminder appears, no token bloat.
4. **Add `/compact` guidance + a goal/state scratchpad convention.** *Verify:* a long test session holds the line better than baseline.
5. **Decide scope**, per-project (this repo's settings) vs global (user settings). *Verify:* lands in the right settings file.

## Open questions
- Which session(s) showed the drift? (need the PC session to analyse)
- Re-injection cadence: every turn (simple, slightly more tokens) vs every N turns / trigger-based.
- Scope: global user settings vs per-project.

## Related
- Machine-local memory note (laptop only): `followup_long-session-drift.md` in this project's `.claude` memory dir. This synced file is the cross-machine copy.

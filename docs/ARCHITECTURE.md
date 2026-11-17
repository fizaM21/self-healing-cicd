# Architecture

## Why two graphs, not one
LangGraph can pause at a human step, but a paused process is lost on restart. Here the **analysis graph** ends at `awaiting_approval` and the state lives in PostgreSQL. The **recovery graph** starts when a person approves. Restarts never lose a waiting approval, and approval is an atomic `UPDATE ... WHERE status='awaiting_approval'`, so double clicks cannot start two recoveries.

## Analysis graph
`collect_logs → classify → recall_memory → analyze → validate → propose`
Each node is wrapped by `safe()`: an exception marks the incident `error`, writes the audit log, and stops the graph.

## Recovery graph
`sandbox_test → create_pr | record_failure`

## Incident states
`detected → analyzing → awaiting_approval → applying → testing → pr_created`
Side exits: `needs_manual` (no safe patch), `rejected`, `failed` (tests failed), `error`.

## Failure memory
`signature = sha256(normalize(error lines))`. Normalizing removes timestamps, hashes, numbers and paths so one bug always gets one signature. Exact hit with more successes than failures: reuse the stored patch (no LLM). Otherwise similar entries (Jaccard ≥ 0.6) go into the prompt as hints. Only patches that passed tests are stored.

## Patch format
`{"edits":[{"path","search","replace"}]}`. Search/replace beats unified diffs: LLMs often get diff line numbers wrong, and `search` must match exactly once, so a wrong patch fails loudly instead of corrupting a file. Empty `search` creates the file or appends.

## LLM fallback
`Analyzer` tries Groq/Gemini; on any error it falls back to offline rules, so a quota outage never stops the pipeline.

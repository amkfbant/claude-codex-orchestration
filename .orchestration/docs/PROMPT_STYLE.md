# Prompt style for v2.1

This kit uses asymmetric prompt contracts. Codex CLI official docs describe stdin/prompt input and JSON output schema, but do not currently provide Codex-specific XML tag guidance, so XML-style input boundaries are marked `[要検証]` and should be eval-tested in your environment:

```text
Input to Codex: Markdown/plain text plus XML-style structural markers.
Output from Codex: JSON only, enforced by --output-schema and hand-rolled validators.
```

XML-style tags are used only where boundaries matter: specs, patches, retry context, static review JSON, validation JSON, and parallel-worker path contracts. Short role text, task objective, ledger acceptance text, and hard constraints stay as plain Markdown because they are easier to read and do not contain nested logs or diffs.

## Tag naming rules

- Tags use snake_case.
- Attributes are minimal and flat.
- Text that may contain `<`, Markdown fences, stack traces, JSON, or diffs is wrapped in `<![CDATA[...]]>`.
- Nesting should stay around two levels.
- The same tag name has the same meaning across dispatch and review prompts.

## Tags

| Tag | Meaning | Notes |
|---|---|---|
| `<spec>` | Authoritative task spec.md | CDATA; read-only for Codex implementation. |
| `<manager_context>` | Optional Claude manager context | CDATA; used for ad hoc clarification. |
| `<allowed_paths>` | Paths this worker may edit | Contains `<path>`. |
| `<assigned_paths>` | Paths assigned to one parallel worker | Contains `<path>`. |
| `<forbidden_paths>` | Paths assigned to other workers in the same batch | Contains `<path>`. |
| `<shared_resources_locked>` | Shared resources not touched in this batch | Contains `<path>`. |
| `<retry_dispatch_context>` | Structured retry data | Contains task context, previous failure, and retry strategy. |
| `<previous_failure>` | Prior stderr, validation, static review, Codex review findings | Uses CDATA for stderr and findings messages. |
| `<retry_strategy>` | Claude-authored retry strategy | CDATA; Markdown allowed. |
| `<agents_md>` | Repository agent rules | Review prompt only; CDATA. |
| `<static_review_result>` | Static review JSON | CDATA. |
| `<validation_result>` | Validation JSON | CDATA. |
| `<patch>` | Implementation diff or format-patch output | CDATA, with `truncated` and `full_bytes` attributes. |
| `<finding>` | Review or static finding | Attributes may include `severity`, `category`, `file`, and `rule`. |

## CDATA conditions

Use CDATA for:

- stderr/stdout excerpts
- diffs and patches
- Markdown documents
- JSON embedded as context
- logs and stack traces
- freeform retry strategy text

The helper splits embedded `]]>` sequences so the prompt remains well-formed enough for boundary reading.

## XML input, JSON output

Codex must never return XML for the final message. Final output is still validated against:

```text
.orchestration/schemas/task-output.schema.json
.orchestration/schemas/codex-review.schema.json
```

If Codex returns XML, Markdown, or prose instead of JSON, validation fails and the task is retried with a narrower instruction.

## Why not full XML?

Full XML adds noise to short fields such as role description, objective, acceptance summary, and fixed constraints. v2.1 uses XML only for long or collision-prone sections.

## Why not all Markdown?

Markdown fences can appear inside patches and Markdown files. Review prompts are especially vulnerable to fence collisions, so v2.1 uses XML-style section markers and CDATA there.

## v3.0 additional tags

- `<learned_lessons>`: project-specific traps from `.orchestration/LEARNED.md`, size-capped before injection.
- `<test_first_phase>`: phase-specific instructions for test-first dispatch.
- `<phase1_tests>` / `<test_file path="...">`: Phase 1 tests shown to Phase 2.

Codex final output remains JSON only. XML tags in final JSON strings are not forbidden, but XML-shaped non-JSON output is rejected by schema validation.

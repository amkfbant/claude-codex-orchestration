# Failure modes

| Failure mode | Detection | Prevention | Recovery |
|---|---|---|---|
| Spec and code drift | Codex semantic review reports `spec_drift`; static review shows unexpected touched paths | Require approved `spec.md` before dispatch; embed spec in prompt | Update spec first, approve it, then rerun dispatch or create fix-up task |
| Stale spec | Implementation requires behavior not in spec | `codex-review` rejects request as spec drift | Move task back to `spec_draft`, update spec, re-approve |
| Test omission bypass abuse | Frequent `--no-spec`, `--allow-legacy`, or `--skip-codex-review` events in `progress.jsonl` | Treat bypass flags as exceptional and audit-logged | Stop batch work, require user approval for future bypasses |
| Production change without tests | `review_diff_impl` high severity `missing-tests-for-production-change` | Require tests for feature/bugfix/behavior/api/security/performance specs | Add tests or change spec kind only if truly non-behavioral |
| Codex review false approval | Reviewer says approve despite static findings or missing tests | Review prompt cross-checks static review, validation, spec, and patch; merge arbiter rechecks schema | Claude manually reviews high-risk patches; create second review with different model |
| Review-dispatch cycle | Same task repeatedly rejected by `codex-review` | Stuck detector and retry thresholds | Split task, narrow spec, or ask user after repeated failures |
| `init-detect` stack misclassification | Recommended commands do not match repo conventions | Present detection JSON to user before applying commands | Edit commands manually with `task-ledger set-commands` or rerun `init` |
| Brownfield state pollution | Existing `CLAUDE.md`/`AGENTS.md` overwritten or application code touched during setup | `init-detect` reports collisions; init only appends marked `.gitignore` block | Restore from backups, inspect `git diff`, reapply with manual merge |
| Semantic reviewer writes files | Git status changes after read-only review | Use `--sandbox read-only`; monitor dirty worktree | Reject review, clean worktree, investigate sandbox config |
| Empty test command hides missing coverage | `validation.json` contains test warning | Require project-specific test command during init | Set test command and rerun validation |
| Codex XML tags leak into final output | `validate_codex_final` / `validate_codex_review` reject non-JSON or schema-mismatched output | Keep output contract as JSON only and pass `--output-schema` | Retry with a narrower prompt reminding Codex that XML is input-only and final output is JSON-only |
| Patch truncation overlooked | `review-exit.json.patch_truncated=true`; `<patch truncated="true">` in reviewer prompt | Use explicit truncation notice and configurable `--review-patch-max-bytes` | Raise `--review-patch-max-bytes` and rerun `codex-review`, or split task |
| Retry context grows too large | Prompt file size or `codex.stderr.log` tail exceeds practical budget | Include bounded stderr tail and summarized validation/review artifacts only | Claude summarizes prior failures, writes a concise retry strategy, and reruns with `--retry-context` |

## v3.0 additional failure modes

| Failure mode | Detection | Prevention | Recovery |
|---|---|---|---|
| Phase1 false failure: meaningless tests or unrelated environment failure | Phase1 validation fails but test diff is trivial or unrelated; semantic review flags missing coverage | Require changed test files, static review, and semantic review, not just exit code non-zero | Rewrite Phase1 test spec or rerun with narrower test paths |
| Phase2 rewrites Phase1 tests to pass | `phase2_changed` includes Phase1 test paths | Phase2 prompt marks Phase1 tests as forbidden; orchestrator rejects rewritten tests | Revert Phase2 commit and retry Phase2 with stronger prompt |
| LEARNED.md bloat | prompt size grows, lessons become unrelated | Cap injection to recent content and require user approval | Archive or prune stale lessons manually |
| Lesson auto-suggestion abuse | frequent `task.escalated_to_user` events with low-value lessons | Claude must ask before adding lessons | Delete noisy lesson entries before commit |
| manager-lock stale detection failure | `manager-status` shows expired or impossible PID/host | TTL and `--force` escape hatch | Run `manager-lock --force` after confirming no other manager is active |
| audit.jsonl/progress.jsonl inconsistency | event exists in one log but not the other | append audit immediately after progress for whitelisted events | Use `audit show` and `progress.jsonl` together; rebuild audit manually if needed |
| Parallel managers double-write ledger | rapid conflicting writes or mismatched updated_at | advisory manager lock plus atomic write_json | Restore from backup or rebuild ledger from progress.jsonl |
| Spec/code drift after spec history update | `spec diff` shows acceptance changed after implementation | require spec approval before dispatch | create follow-up task or re-dispatch with updated spec |
| Test-first phase confusion on retry | failed task has phase1_done but phase2 failed | phase_state is stored in ledger and phase artifacts are separated | retry Phase2 from existing tests; do not rerun Phase1 unless tests are wrong |

## v3.0 failure modes

| Failure mode | Detection | Prevention | Recovery |
|---|---|---|---|
| Phase1 tests are fake failures | Phase1 validation only checks non-zero plus semantic review findings | Review test intent with Codex reviewer and manager | Mark failed, retry Phase1 with stricter spec/test cases |
| Phase2 weakens Phase1 tests | `phase2-touched-phase1-tests` finding | Inject Phase1 tests into prompt and forbidden paths | Reject or manually inspect, then retry Phase2 |
| `LEARNED.md` grows stale or too large | prompt injection truncation notice / file size | User-approved lessons only, concise entries | Archive irrelevant lessons manually |
| Lesson proposal spam | escalation frequency and low-value lesson candidates | Claude must ask user before adding lessons | Decline or consolidate lessons |
| manager-lock stale detection failure | `manager-status` shows expired or unexpected lock | TTL and `--force` available | Use `manager-lock --force` after confirming no other manager |
| audit/progress inconsistency | compare `audit.jsonl` and `progress.jsonl` for high-risk events | append both from one `append_event` call | Reconstruct missing audit events from progress if needed |
| parallel managers double-write ledger | manager-status warning, unexpected ledger changes | advisory lock and user awareness | stop one manager, rebuild ledger from progress |
| test-first Phase1/Phase2 retry confusion | `phase_state` and phase artifacts disagree | phase-specific artifact directories | resume from last successful phase; preserve Phase1 tests |

## v3.0 F10 Codex project configuration failure modes

| Failure mode | Detection | Prevention | Recovery |
|---|---|---|---|
| Project not trusted, so `.codex/config.toml` is ignored | `codex-status` shows `trust_state` as `unknown` or `untrusted`, and `effective_model_source` is not `project` despite project config existing | Run `codex` once from repo root during init and accept the trust prompt if shown | Run `codex`, trust the project, then re-run `.orchestration/bin/codex-status --suggest` |
| User config and project config specify different models | `codex-status` displays both `user_config_model` and `project_config_model` plus the inferred source | Make model source explicit in runbooks; use `--model` for one-off dispatches | Update `.codex/config.toml`, or pass `--model` / `--review-model` explicitly |
| Model catalog changes after config was committed | Dispatch metadata in `exit.json.effective_model` no longer matches a usable model | Keep `.codex/config.toml` placeholders or review `.orchestration/docs/MODEL_GUIDE.md` before uncommenting | Use `codex debug models` or official model docs, then update project config |
| Personal credentials committed under `.codex/` | `.gitignore` block includes `.codex/auth.json`, `.codex/sessions/`, `.codex/log/`, and `.codex/config.local.toml` | Keep secrets and local preferences in ignored files only | Remove from git history according to your organization's secret-rotation policy |

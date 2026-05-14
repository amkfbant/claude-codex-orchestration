# Codex Model Guide

This guide intentionally avoids hard-coding concrete model names. Codex model availability changes over time and can vary by account, workspace, provider, and release channel.

## Check current model availability

Preferred options:

```bash
codex debug models
codex debug models --bundled
```

If your current Codex CLI does not expose the debug model catalog, consult the official Codex Models documentation and your account/workspace model selector.

## Where to set the model

Precedence, highest to lowest:

1. CLI flags and `-c/--config` overrides, for example `codex exec --model <name>`
2. `--profile <name>` profile values
3. Project config `.codex/config.toml`, only after the project is trusted
4. User config `~/.codex/config.toml`
5. System config, if present
6. Codex built-in defaults

This kit never edits `~/.codex/config.toml`. For project-scoped defaults, copy `.codex/config.toml` and uncomment only the keys you need.

## Implementation vs review model strategy

- Implementation dispatches should use a strong current coding model available to your account.
- Semantic review can use a different model or profile to reduce correlated mistakes.
- For security, API, and migration tasks, prefer a reviewer model/profile with equal or greater reasoning depth than the implementation model.
- For small docs/config tasks, relying on the default model is often acceptable.

## Suggested project config pattern

```toml
# .codex/config.toml
# model = "<implementation-model-name>"

# [profiles.review]
# model = "<review-model-name>"
# approval_policy = "never"
# sandbox_mode = "read-only"
```

Then run:

```bash
.orchestration/bin/codex-dispatch <task-id> --model "<implementation-model-name>" --review-model "<review-model-name>"
```

or rely on project defaults and inspect them with:

```bash
.orchestration/bin/codex-status --suggest
```

## Spec kind strategy template

| Spec kind | Implementation strategy | Review strategy |
|---|---|---|
| `feature` | strong coding model | different strong reviewer when available |
| `bugfix` | model with good debugging performance | reviewer checks regression tests and root cause |
| `api` | model with strong contract reasoning | reviewer focuses on spec drift and compatibility |
| `security` | strongest available coding/security model | independent reviewer, preferably equal or stronger |
| `refactor` | efficient coding model may be enough | reviewer checks behavior preservation |
| `docs` / `config` | default may be enough | static review plus optional semantic review |

## Trust reminder

Project-scoped `.codex/config.toml` is only loaded after the project is trusted. Run `codex` once from the repository root and accept the trust prompt if shown.

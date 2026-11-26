# Review benchmark results

| Mode | Caught targets | Recall | Clean PRs with a false alarm | Mean seconds/PR | Mean model USD/PR | Incomplete PRs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Deterministic | 17/27 | 63.0% | 0/10 | 0.282 | 0.00000000 | 0 |
| Model-assisted | 18/27 | 66.7% | 1/10 | 4.730 | Unavailable | 0 |

Model-assisted cost is unavailable for 16 PR(s) with missing usage or rates.

## Run settings

- Started (UTC): 2026-11-26T07:30:14+00:00
- Dataset SHA-256: ab838c081d90a5739eca4ebaee85d07e48b5720af064f6a645f1d5569fefcbec
- Runtime: Windows AMD64, Python 3.12.13
- Tool versions: ai-code-review-guardrail 0.1.0, ruff 0.17.6, bandit 1.9.4
- Deterministic checks: imports, exception-handling, secrets, test-reference, no-test-changes
- Timing: Wall time from patch/source loading through review, excluding checkout setup and scoring. Cases run serially.
- Findings included: All severities
- Model prompt: diff-edge-case-review 1.0.0
- Model selection: Flagged Python hunks or at least 12 added code lines
- Request limits: 16384 input bytes, 65536 maximum reply bytes, 120s timeout, one CLI invocation
- Provider / requested model: Codex CLI / gpt-6-sol (medium reasoning)
- Provider requests: 16. Returned model versions: unavailable.
- Rates in USD per million tokens: input unavailable, cached_input unavailable, output unavailable.

## Case outcomes

| Case | Target | Deterministic | Model-assisted | Model USD |
| --- | --- | --- | --- | --- |
| case-001 | Clean | No finding | No finding | 0.00000000 |
| case-002 | Clean | No finding | No finding | 0.00000000 |
| case-003 | off-by-one | Missed | Missed | 0.00000000 |
| case-004 | missing-none-handling | Missed | Caught | Unavailable |
| case-005 | off-by-one | Missed | Missed | Unavailable |
| case-006 | test-reference | Caught | Caught | Unavailable |
| case-007 | missing-none-handling | Missed | Missed | 0.00000000 |
| case-008 | missing-none-handling | Missed | Missed | 0.00000000 |
| case-009 | Clean | No finding | No finding | 0.00000000 |
| case-010 | off-by-one | Missed | Missed | 0.00000000 |
| case-011 | missing-none-handling | Missed | Missed | 0.00000000 |
| case-012 | exception-handling | Caught | Caught | Unavailable |
| case-013 | imports | Caught | Caught | Unavailable |
| case-014 | imports | Caught | Caught | Unavailable |
| case-015 | test-reference | Caught | Caught | Unavailable |
| case-016 | secrets | Caught | Caught | 0.00000000 |
| case-017 | secrets | Caught | Caught | 0.00000000 |
| case-018 | Clean | No finding | No finding | 0.00000000 |
| case-019 | off-by-one | Missed | Missed | 0.00000000 |
| case-020 | exception-handling | Caught | Caught | Unavailable |
| case-021 | off-by-one | Missed | Missed | 0.00000000 |
| case-022 | imports | Caught | Caught | Unavailable |
| case-023 | Clean | No finding | No finding | 0.00000000 |
| case-024 | Clean | No finding | No finding | 0.00000000 |
| case-025 | secrets | Caught | Caught | 0.00000000 |
| case-026 | Clean | No finding | No finding | 0.00000000 |
| case-027 | exception-handling | Caught | Caught | Unavailable |
| case-028 | Clean | No finding | No finding | 0.00000000 |
| case-029 | Clean | No finding | False alarm | Unavailable |
| case-030 | exception-handling | Caught | Caught | Unavailable |
| case-031 | test-reference | Caught | Caught | Unavailable |
| case-032 | secrets | Caught | Caught | 0.00000000 |
| case-033 | Clean | No finding | No finding | 0.00000000 |
| case-034 | imports | Caught | Caught | Unavailable |
| case-035 | test-reference | Caught | Caught | Unavailable |
| case-036 | missing-none-handling | Missed | Missed | 0.00000000 |
| case-037 | no-test-changes | Caught | Caught | Unavailable |

# Review benchmark results

| Mode | Caught targets | Recall | Clean PRs with a false alarm | Mean seconds/PR | Mean model USD/PR | Incomplete PRs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Deterministic | 17/27 | 63.0% | 0/10 | 0.232 | 0.00000000 | 0 |
| Model-assisted (awaiting live run) | — | — | — | — | — | — |

## Run settings

- Started (UTC): 2026-11-25T17:45:26+00:00
- Dataset SHA-256: ab838c081d90a5739eca4ebaee85d07e48b5720af064f6a645f1d5569fefcbec
- Runtime: Windows AMD64, Python 3.12.13
- Tool versions: ai-code-review-guardrail 0.1.0, ruff 0.17.6, bandit 1.9.4
- Deterministic checks: imports, exception-handling, secrets, test-reference, no-test-changes
- Timing: Wall time from patch/source loading through review, excluding checkout setup and scoring. Cases run serially.
- Findings included: All severities
- Model prompt: diff-edge-case-review 1.0.0
- Model selection: Flagged Python hunks or at least 12 added code lines
- Request limits: 16384 input bytes, 2048 output tokens, 30s timeout, zero retries
- Provider / requested model: Awaiting live run

## Case outcomes

| Case | Target | Deterministic |
| --- | --- | --- |
| case-001 | Clean | No finding |
| case-002 | Clean | No finding |
| case-003 | off-by-one | Missed |
| case-004 | missing-none-handling | Missed |
| case-005 | off-by-one | Missed |
| case-006 | test-reference | Caught |
| case-007 | missing-none-handling | Missed |
| case-008 | missing-none-handling | Missed |
| case-009 | Clean | No finding |
| case-010 | off-by-one | Missed |
| case-011 | missing-none-handling | Missed |
| case-012 | exception-handling | Caught |
| case-013 | imports | Caught |
| case-014 | imports | Caught |
| case-015 | test-reference | Caught |
| case-016 | secrets | Caught |
| case-017 | secrets | Caught |
| case-018 | Clean | No finding |
| case-019 | off-by-one | Missed |
| case-020 | exception-handling | Caught |
| case-021 | off-by-one | Missed |
| case-022 | imports | Caught |
| case-023 | Clean | No finding |
| case-024 | Clean | No finding |
| case-025 | secrets | Caught |
| case-026 | Clean | No finding |
| case-027 | exception-handling | Caught |
| case-028 | Clean | No finding |
| case-029 | Clean | No finding |
| case-030 | exception-handling | Caught |
| case-031 | test-reference | Caught |
| case-032 | secrets | Caught |
| case-033 | Clean | No finding |
| case-034 | imports | Caught |
| case-035 | test-reference | Caught |
| case-036 | missing-none-handling | Missed |
| case-037 | no-test-changes | Caught |

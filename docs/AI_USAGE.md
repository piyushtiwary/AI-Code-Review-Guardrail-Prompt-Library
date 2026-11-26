# AI-assisted changes and verification

## Steps 19 and 20

Codex added the Windows and Linux quality jobs, configured the 85% pytest-cov threshold, and wrote the installed-wheel smoke check. It also revised the quick start and captured the CLI and sample PR outputs. The test suite, review checks, prompt examples and measured benchmark data already existed when this work began.

| Change | How it was checked |
| --- | --- |
| Run the full suite in CI | An initial installation with the development extra skipped three SDK adapter tests. Installing the model extra enabled those tests against their mock HTTP transport. That configuration applied to the original API provider. The Codex CLI adapter now uses a subprocess fixture with the development extra. |
| Check the distributable package | The smoke check builds a wheel, installs it with runtime dependencies in a new virtual environment, and runs the CLI from a temporary directory with spaces in its name. It replays all three prompt examples and compiles the two-scenario scaffold rendered by the packaged Jinja template. |
| Rehearse the CLI instructions | The rehearsal used local Git clones of a snapshot that included the pending benchmark files, and fresh Python 3.12 environments. The clean review exited 0. The defective review exited 1 with warnings at lines 1 and 7. The scaffold collected two tests, both failed at the stated TODO, and all three offline prompt examples passed. [Terminal recording](cli-demo.cast) |
| Exercise PR publication | The Action entry point reviewed two real Git commits in a local sample consumer, using a loopback GitHub API fixture. At the blocking threshold it created one comment with two warnings, then edited the same comment after the clean commit. The local capture substitutes for a hosted consumer run in this checkout. [Captured comment bodies](sample-pr-summary.md) |
| Document benchmark results | The figures in the README come from the saved per-case run. The suite regenerates the summary and compares it with the published report. The first benchmark recorded the deterministic mode. The Codex CLI run below adds paired live observations. [Saved observations](../benchmarks/results/run.json) · [Benchmark report](../benchmarks/results/summary.md) |

The full local suite passed **202 tests** on each platform. Package coverage reached **92.68%** on Windows with Python 3.12.13 and **92.62%** on Debian under WSL2 with Python 3.12.16. Both checkout paths contained spaces.

Two negative controls exercised the same pytest configuration used by CI. All 16 patch parser tests passed when run alone, and the coverage gate failed at 7.26%. In a disposable clone, adding one incorrect line to the expected clean report made the full suite exit unsuccessfully with one fixture failure, 201 passing tests and 92.62% coverage.

## Codex CLI integration

Guardrail ran all three prompt examples live using Codex CLI 0.172.0, model `gpt-6-sol` and medium reasoning. Each response passed its existing output contract. The paired benchmark recorded 37 complete reviews per mode, including 16 live model invocations. Model observations were categorized from their messages before scoring. [Measured results](../benchmarks/results/summary.md)

On GitHub, the first sample revision generated three warnings. A clean follow-up updated the existing bot comment. Both Action runs passed at the default blocking threshold. [Initial run](https://github.com/piyushtiwary/AI-Code-Review-Guardrail-Prompt-Library/actions/runs/45776157510), [clean run](https://github.com/piyushtiwary/AI-Code-Review-Guardrail-Prompt-Library/actions/runs/45776244182), [bot comment](https://github.com/piyushtiwary/AI-Code-Review-Guardrail-Prompt-Library/pull/1#issuecomment-7185582684)

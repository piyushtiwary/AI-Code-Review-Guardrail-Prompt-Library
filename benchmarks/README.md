# Labeled review benchmark

The dataset contains 37 independent changes to four small Python projects. Each of the 27 defective changes has one target. The 10 clean changes exercise similar code, including exact limits, optional values, credential lookalikes and handled exceptions.

Each project under `inputs/` has a `base/` checkout and a `patches/` directory. Apply each patch to a fresh copy of its sibling base. The patch filename gives the case ID. Only the patch and its matching checkout are reviewer inputs.

[`labels.yaml`](labels.yaml) is the scoring key. Each record contains the case ID, a target and a short explanation. Clean cases use `target: null`. For a defect, each anchor is an acceptable location for the same target, with a repository-relative path and a 1-based added line in the post-change file. All credential examples are synthetic.

| Target category | Cases | Example target | Clean comparison |
| --- | ---: | --- | --- |
| `imports` | 4 | 034 imports an absent local currency symbol | 002 imports a valid local symbol under an alias |
| `test-reference` | 4 | 006 adds a method while testing only construction | 024 calls the new method in a test |
| `exception-handling` | 4 | 012 swallows a transport failure | 009 logs and re-raises it |
| `secrets` | 4 | 016 embeds a token-shaped literal | 033 uses an environment reference alongside a UUID |
| `off-by-one` | 5 | 010 returns a row for a zero-sized preview | 026 returns an empty preview |
| `missing-none-handling` | 5 | 004 calls a string method on an omitted depot | 029 accepts the default argument |
| `no-test-changes` | 1 | 037 changes the booking limit without a test edit | 001 includes the same logic and tests its boundaries |

## Review one case offline

From the repository root with Guardrail installed, copy a base to a fresh sibling directory, apply one patch and point the reviewer at that checkout. This example exercises the clean preview boundary.

```sh
cp -r benchmarks/inputs/exports/base ../guardrail-preview-case
git -C ../guardrail-preview-case init -q
cp benchmarks/inputs/exports/patches/case-026.patch ../guardrail-preview-case/change.patch
git -C ../guardrail-preview-case apply change.patch
guardrail review --diff ../guardrail-preview-case/change.patch --source-root ../guardrail-preview-case --no-llm
```

Validate the inventory, patch application, Python syntax and target locations from the repository root with the development dependencies installed.

```sh
python -m pytest -q tests/test_benchmark_dataset.py
```

## Measure and score the reviewer

The [measured report](results/summary.md) includes the run settings, aggregate metrics and a verdict for each case. The [saved observations](results/run.json) contain the findings and elapsed time for each review. Regenerate the report from the repository root, without credentials or a new run.

```sh
guardrail benchmark score benchmarks/results/run.json --out summary.md
```

For a fresh deterministic measurement, run:

```sh
guardrail benchmark run --out offline-run.json
guardrail benchmark score offline-run.json --out offline-summary.md
```

For a paired run, install the model extra and set `OPENAI_API_KEY` and `GUARDRAIL_MODEL` as described in the [project README](../README.md#optional-model-review). Use a dated model ID when available. Supply the rates applicable to your model and account in USD per million tokens using `--input-rate`, `--cached-input-rate` and `--output-rate`. Rates are optional. Without an applicable rate or token count the report marks cost unavailable.

```sh
guardrail benchmark run --live --out paired-run.json
```

The runner copies and patches each base once, then measures the deterministic reviewer and the model-assisted reviewer independently on that checkout. Both use the default checks, full source and all finding severities. Timing covers source loading, checks and any provider request. Checkout preparation, reporting and grading are outside the timer. Live records include the selected prompt version, requested model, returned model ID when available, request status and reported token usage. A PR with no provider request costs zero. For a request, the cost is the sum of uncached input, cached input and output token counts multiplied by their respective rates, divided by one million.

### Assign categories and score a live run

The production review prompt returns a generic check ID and a description. To grade it blind, copy `paired-run.json` to `paired-graded.json` before opening the scoring key. For each finding whose `check` is `diff-edge-case-review`, add a `category` field based on the defect described in its message. Use a category from the table above, or `other` for a different or unsupported diagnosis. For example, a finding explaining that an optional depot is passed to a string method can receive `"category": "missing-none-handling"`. Keep the reported path and line. Deterministic findings already identify their category.

```sh
guardrail benchmark score paired-graded.json --out paired-summary.md
```

A target counts once if any finding matches its category and an accepted path/line pair. Any finding on a clean PR counts as one false alarm for that PR. Recall uses all 27 targets and the false-alarm count uses all 10 clean PRs. Incomplete reviews retain their observed findings and are marked partial. Unclassified model findings receive no target credit until graded. Missing cost data on any PR makes the overall mean cost unavailable. The runner saves incomplete outcomes and exits with status 3 when an operational failure or incomplete review occurs.

The hand-counted regression uses cases 012, 004 and 026. Duplicate reports on 012 catch one target. Reports on 004 with the wrong line, file or category earn zero hits. Two reports on clean case 026 flag one PR. Together these give 1/2 targets caught (50% recall) and 1/1 clean PRs with a false alarm. [Scoring tests](../tests/test_benchmark.py)

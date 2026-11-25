# Guardrail

## Run the offline examples

With Git and an active Python 3.12 environment, run `python -m pip install -e .` from the repository root. After installation, both examples run without network access or credentials.

The example checkout contains a local module with one default quantity. Each patch adds a quantity parser and its tests. From the repository root, copy the checkout into two new sibling directories so each review reads the matching post-change files. These commands work in PowerShell and POSIX shells.

~~~sh
cp -r examples/offline/checkout ../guardrail-clean
cp examples/offline/clean/changes.patch ../guardrail-clean/changes.patch
cp -r examples/offline/checkout ../guardrail-defective
cp examples/offline/defective/changes.patch ../guardrail-defective/changes.patch

cd ../guardrail-clean
git init -q
git apply changes.patch
guardrail review --diff changes.patch --no-llm
~~~

The clean review finds the imported constant, sees the test reference, and accepts the narrow `ValueError` handler. It prints `No findings.` and exits with status **0**. [Expected clean report](examples/offline/clean/expected.txt)

~~~sh
cd ../guardrail-defective
git init -q
git apply changes.patch
guardrail review --diff changes.patch --no-llm
~~~

The defective review reports an invalid import at `quantities.py:1` and a swallowed exception at `quantities.py:7`. Both are warnings, so the default threshold gives exit status **1**. Read the status immediately after either review with `$LASTEXITCODE` in PowerShell or `echo $?` in a POSIX shell. [Expected defective report](examples/offline/defective/expected.txt)

On Linux with unprivileged user and network namespaces enabled, repeat the review from either prepared checkout with networking disabled.

~~~sh
unshare --user --map-root-user --net guardrail review --diff changes.patch --no-llm
~~~

## Choose the source for a review

From a Git checkout, compare a feature branch with its base.

~~~sh
guardrail review --base main --head HEAD
~~~

Guardrail diffs the merge base against the specified head commit. It reads complete Python files, including unchanged tests, from that same commit. You can run the command with another branch checked out or with local edits.

To save the same comparison and review it later, write the patch directly with Git. Run the review from the matching post-change checkout, or select it with `--source-root`.

~~~sh
git diff --full-index --output=changes.patch main...HEAD
guardrail review --diff changes.patch
guardrail review --diff changes.patch --source-root "/path/to/feature checkout"
~~~

Saved-patch mode uses working-tree contents. It checks Git post-change blob IDs when present and checks hunk text at the recorded new-file lines. For a plain unified diff, content outside the hunks and files absent from the patch must come from the checkout you selected. The Python source set includes tracked files and non-ignored untracked files.

When only the patch is available, run this from any directory.

~~~sh
guardrail review --diff changes.patch --patch-only
~~~

The report gives the changed-file and added-line counts. If the checkout is missing, disagrees with the patch, or provides too little evidence to verify a post-change file, the report marks whole-file checks as skipped and gives the reason. For example, a deletion-only patch identifies no surviving file to match. A malformed patch or invalid revision produces an input error and exits with status 3.

## Review findings and exit status

The report lists findings by repository-relative file and, when available, 1-based line in the post-change file. Each entry includes the check name, severity, message, and a suggestion when supplied.

The built-in `imports` check inspects added Python imports, including aliases, relative imports, and names added to multiline imports. It searches the reviewed source at the repository root and under `src/`, recognizes standard-library names, and uses the review environment's installed distribution metadata for third-party import names. Install the project's dependencies in that environment for the most accurate results.

An unresolved module or a missing name in a simple local module produces a warning. The check leaves external package contents and dynamic exports (such as a module-level `__getattr__` or a wildcard re-export) unverified. It reads source without importing reviewed modules. Patch-only reviews skip this check.

The `exception-handling` check warns on added bare `except` clauses and broad handlers that swallow unexpected errors, such as `except Exception: pass` or a silent fallback return. It combines Ruff (E722, BLE001) and Bandit (B110, B112) into one warning per affected handler, anchored on an added line. A narrowly handled expected error or a typed handler that logs and re-raises stays quiet. Both scanners run locally against the selected post-change source. Patch-only reviews skip the check. A scanner failure reports the tool and exits with status 3.

The `secrets` check runs on added lines in every text file, including in patch-only reviews. It reports one blocking finding per line containing a recognizable GitHub token, AWS access key ID, Slack token, Stripe key, or high-entropy literal assigned to a credential-like name such as `API_KEY` or `password`. The assignment heuristic looks for at least 16 characters and 3.5 bits of Shannon entropy per character. It skips ordinary UUIDs and leaves checksums in checksum fields alone. Findings give the location. Review output and prepared model excerpts mask detected values as `[REDACTED]`, including repeated values in hunk context and removed lines.

### Test-related warnings

- `test-reference` checks each new Python function or method outside test files, including async definitions. It searches the complete head revision's tests (or the matching checkout's tests) for a loaded name, attribute, or explicit import matching the function's name. An unchanged test can satisfy the check. The reference heuristic can match another function with the same name or miss an indirect call. The warning points to the new definition. Patch-only reviews skip this check.
- `no-test-changes` emits one warning for the entire patch when executable Python logic changes without any test-file change. An unrelated test edit clears this warning. Comments, docstrings, and documentation files alone produce no warning. The location is the first added logic line in patch order. If the patch only removes logic, the report shows the first affected file without a line number, using the former path for a deleted file. This check also runs in patch-only mode, where classification is limited to the context present in the hunks.

Both checks recognize Python files under a `test/` or `tests/` directory and files named `test_*.py`, `*_test.py`, `tests.py`, or `conftest.py`.

### Exit thresholds

Use `--fail-on blocking` (the default) or `--fail-on warning` with either review input mode.

| Review outcome | `--fail-on blocking` | `--fail-on warning` |
| --- | ---: | ---: |
| No findings or informational findings only | 0 | 0 |
| Warning is the highest severity | 1 | 2 |
| At least one blocking finding | 2 | 2 |
| Review input or check execution failed, including invalid check output | 3 | 3 |

Operational diagnostics go to standard error. A partial review applies the same thresholds to findings from the checks that ran.

## Review pull requests with GitHub Actions

In a consumer repository, save this workflow as `.github/workflows/guardrail.yml`. Replace `OWNER/GUARDRAIL_REPO` with the repository publishing this action and `FULL_COMMIT_SHA` with a trusted commit containing `action.yml`.

~~~yaml
name: Guardrail PR review
on: pull_request
permissions:
  contents: read
  pull-requests: write
concurrency:
  group: guardrail-pr-${{ github.event.pull_request.number }}
  cancel-in-progress: true
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: OWNER/GUARDRAIL_REPO@FULL_COMMIT_SHA
        with:
          comment: 'true'
~~~

The action installs Guardrail from the selected action revision. It fetches both event SHAs and their history into a temporary bare Git repository. This project's own workflow loads the action from the PR base commit, so it can review subsequent PRs once the action is present on the base branch. [Repository workflow](.github/workflows/guardrail.yml)

| Input | Default | Effect |
| --- | --- | --- |
| `fail-on` | `blocking` | Warnings below the threshold produce a job warning and pass. Set to `warning` to fail on warnings too. Blocking findings and operational errors fail the job. |
| `use-llm` | `false` | Set to `true` and supply `OPENAI_API_KEY` and `GUARDRAIL_MODEL` in the step environment to enable model review on same-repository PRs. Fork PRs run local checks without a model secret. |
| `comment` | `false` | Set to `true` to post the summary and refresh it on reruns, including after a clean push. |

For log-only reviews, omit `comment` and grant only `contents: read`. When commenting is enabled, the CLI prints the report first. If GitHub denies the comment with HTTP 403, the log records the limitation and the review keeps its normal exit status. This supports the default read-only workflow token on fork PRs.

To publish from a CLI workflow step, set `GITHUB_TOKEN` to `${{ github.token }}` and pass the PR number to `guardrail review`.

~~~sh
guardrail review --base "$BASE_SHA" --head "$HEAD_SHA" --comment-on-pr "$PR_NUMBER"
~~~

Supply the event's base SHA, head SHA, and PR number as step environment variables. The CLI reads the destination from `GITHUB_REPOSITORY` or `--repository OWNER/REPO`. It updates a PR conversation comment only when its first line carries Guardrail's marker and GitHub identifies its author as `github-actions[bot]`. The per-PR concurrency group serializes publishers.

## Optional model review

Install the provider extra with `python -m pip install -e ".[model]"`. Set `OPENAI_API_KEY` in your environment and `GUARDRAIL_MODEL` to the ID of a text model available to your account through the [OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create). Then opt in on either review input mode.

~~~sh
guardrail review --base main --head HEAD --use-llm
guardrail review --diff changes.patch --patch-only --use-llm
~~~

Local checks run first. A Python hunk qualifies when a local finding points to one of its added lines, or when it contains at least 12 nonblank, non-comment added lines. The second rule uses hunk size as a proxy for complexity. With no eligible hunks, the CLI skips the provider. The default and explicit `--no-llm` modes run offline, including when credentials are present.

Guardrail sends at most one request. It uses the instructions from `diff-edge-case-review` version `1.0.0` and supplies the selected, redacted hunks as user input. In patch order it includes whole hunks that fit within a combined 16 KiB UTF-8 budget for instructions and input. It skips hunks with masked paths or unencodable text. The request has a 30-second deadline, a 2,048-token output limit, and zero retries.

The CLI validates each returned finding independently. A model finding must use the prompt's check ID and match an exact path and added line from the submitted hunks. Accepted findings participate in the usual exit threshold. Omitted eligible hunks, rejected findings, malformed responses, and provider failures mark model review incomplete in the report. Local findings and their exit thresholds still apply.

## Parse acceptance criteria

`guardrail.story.parse_story(text)` accepts a Jira issue heading such as `SHIP-42: Reroute a shipment` or a `Story: <title>` heading. Start each scenario with `Scenario: <name>`, then write its `Given`, `When`, and `Then` clauses in order. Use `And` to continue the preceding clause type. The parser returns each scenario’s conditions, actions, and outcomes in input order. [Shipment rerouting example](stories/reroute.feature)

Misplaced clauses raise `StoryInputError` with the offending line number. If a scenario ends before a required clause, the error names the missing clause and points to the scenario heading.

## Generate pytest scaffolds

From the repository root with the development extra installed (`python -m pip install -e ".[dev]"`), run the shipment example.

~~~sh
guardrail storygen stories/reroute.feature --out tests/generated/
python -m pytest tests/generated/test_reroute.py
~~~

The command creates `test_reroute.py` with one test per scenario. Each test records its steps as Arrange, Act, and Assert comments and ends with `pytest.fail("TODO: implement this scenario")`. Both tests fail until you replace the TODOs with test logic and assertions.

If the destination file already exists, generation exits with status 3 and preserves the file. To compare scaffolds after updating a story, generate into a fresh output directory.

## Render a versioned prompt

After installation, run any of these commands from any directory. Each prints the prompt with its packaged example inputs filled in and requires no credentials.

~~~sh
guardrail prompts render diff-edge-case-review 1.0.0 last-seat
guardrail prompts render endpoint-pytest-scaffold 1.0.0 lookup
guardrail prompts render write-acceptance-criteria 1.0.0 booking-capacity
~~~

To supply your own inputs in Python, select a prompt by its ID and exact version. For diff review, supply redacted hunks in the format returned by `ReviewSource.prepare_model_hunk`.

~~~python
from guardrail.prompts import load_prompts

entry = load_prompts()["write-acceptance-criteria", "1.0.0"]
text = entry.prompt.render({
    "title": "Cancel a booking",
    "requirements": "Confirmed booking: cancelling it releases the reserved seats.",
})
~~~

The [prompt library](src/guardrail/prompt_library) stores each template, its input names, expected output properties, known failure modes, and evaluation history in a versioned YAML file. Each example points to a fixture containing inputs, a hand-written reference output, and separate scoring expectations. When revising a released prompt, add a new semantic version and corresponding fixtures.

## Evaluate the prompt library

`guardrail prompts eval` replays the packaged reference outputs offline. The version 1.0.0 histories record a passing replay for each of the three fixtures. To save the per-example JSON results, redirect standard output.

~~~sh
guardrail prompts eval --offline > replay-results.json
~~~

| Example | What a pass checks |
| --- | --- |
| `last-seat` | Every item is a valid review finding with the prompt's check ID. The paths and line numbers match the expected anchors exactly, including their counts. |
| `lookup` | The module compiles and imports pytest. Each request has a distinct, parameterless top-level test with Arrange, Act and Assert comments, its expected HTTP response, and a final `pytest.fail("TODO: implement this scenario")`. Guardrail inspects the source without executing it. |
| `booking-capacity` | The story title matches, the required scenarios appear in order, and each has nonempty Given, When and Then clauses in order. |

To evaluate fresh responses, install the model extra and set `OPENAI_API_KEY` and `GUARDRAIL_MODEL` as described under **Optional model review**. Live mode sends one rendered prompt per example and applies the same checks.

~~~sh
guardrail prompts eval --live > live-results.json
~~~

Each record identifies the prompt, version, fixture and example, mode, verdict, and reason. Live records also include the provider, configured model, and UTC evaluation date. Exit status **0** means all examples passed, **1** means an output failed its contract, and **3** means a configuration, library, or provider error prevented a complete evaluation.

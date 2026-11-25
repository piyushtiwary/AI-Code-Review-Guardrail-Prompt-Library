# Guardrail

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

The report lists findings by repository-relative file and 1-based line in the post-change file. Each entry includes the check name, severity, message, and a suggestion when supplied. Milestone 1 defines the review framework. Built-in checks are scheduled for Milestone 2, so the default check list is currently empty.

Use `--fail-on blocking` (the default) or `--fail-on warning` with either review input mode.

| Review outcome | `--fail-on blocking` | `--fail-on warning` |
| --- | ---: | ---: |
| No findings or informational findings only | 0 | 0 |
| Warning is the highest severity | 1 | 2 |
| At least one blocking finding | 2 | 2 |
| Review input or check execution failed, including invalid check output | 3 | 3 |

Operational diagnostics go to standard error. A partial review applies the same thresholds to findings from the checks that ran.

# Sample PR summary

Captured from the composite Action entry point using the bundled quantity example, a local Git repository and a loopback GitHub API fixture. Both runs returned job status 0 at the default blocking threshold. The first run created comment 1. After the clean commit, the second run edited that same comment.

### First run · defective patch

<!-- guardrail:pr-summary:v1 -->
## Guardrail review

Skipped checks: none.

<pre>Model review disabled.</pre>

### Findings (2)

**WARNING**

<pre>quantities.py:1  imports
Local module &#x27;defaults&#x27; has no name &#x27;DEFAULT_COUNT&#x27;.
Suggested action: Review the reported change.</pre>

**WARNING**

<pre>quantities.py:7  exception-handling
Broad exception handler silently passes.
Suggested action: Catch the expected exceptions or re-raise unexpected errors.</pre>

### Second run · clean patch

<!-- guardrail:pr-summary:v1 -->
## Guardrail review

Skipped checks: none.

<pre>Model review disabled.</pre>

No findings.

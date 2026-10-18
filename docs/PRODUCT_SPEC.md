# PatchTrace — product spec v0.2

## User
A first-time or occasional open-source contributor who has a specific issue but is unsure where to start, what evidence matters, or which checks are actually available.

## Product promise
Given one repository and one task, produce a bounded, evidence-linked contribution brief, an optional reviewable artifact, and an honest verification ledger.

## Core path
1. Select the labelled synthetic demo or a public HTTPS GitHub repository. Local paths are supported only in local development mode.
2. Submit a concrete issue/task.
3. Review the repository overview and bounded scan coverage.
4. Read ranked candidate paths with exact line references.
5. When enabled and configured, send the task and bounded repository context to Gemma 4 via the hosted API for task summary, likely paths, plan, questions and risks.
6. Download the contribution brief and optional proposed README patch.
7. Explicitly run a supported check and review the pass/fail/not-run ledger.

## Differentiation hypothesis
A useful combination: repository mapping + line references + a narrow reviewable artifact + truthful execution ledger. This is not a claim of category uniqueness or superior results; it must be tested against real tasks and repositories.

## Non-goals for v0.2
- Autonomous implementation across arbitrary repositories
- Auto-commit, push, pull request, or issue posting
- Arbitrary shell command execution
- Claims that model suggestions are verified or superior without evaluation
- Durable multi-user accounts or persistent sessions
- Production-grade hostile-code sandboxing

## Success criteria
- Demo identifies the deliberately stale README quick-start command.
- Every displayed evidence card has a path and positive line number from the scanned snapshot.
- A proposed diff is generated only when the exact README condition is met, and original files remain unchanged.
- The test runner distinguishes not-run, passed and failed based on real process results; public mode prevents arbitrary pytest execution.
- Missing key, quota errors, malformed output and unavailable API are displayed as model limitations while deterministic inspection remains available.
- The live Gemma API response is confirmed using the separate smoke test before claiming the model works in deployment.

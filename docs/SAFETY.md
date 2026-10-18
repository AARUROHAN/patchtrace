# Safety and trust boundaries

PatchTrace inspects bounded repository text, optionally sends selected repository context to a configured AI provider, and can run limited checks. The proposed artifact always requires human review.

- The Gemma API key is read only from backend environment variables (`GEMINI_API_KEY`). Do not place it in browser code, `NEXT_PUBLIC_*` variables, source control, screenshots, or public issues.
- When AI analysis is enabled, the task, repository overview, candidate paths and selected line excerpts are sent to Google's Gemma API. Do not submit private or secret-bearing code unless you have permission and have reviewed the data handling implications.
- Public deployment mode disables local filesystem paths and arbitrary repository pytest execution. Python syntax compilation is offered as a limited check; it does not prove correctness or security.
- The public service has a small in-process POST request throttle and bounded in-memory session count. These are demo safeguards, not production-grade multi-tenant security controls or a distributed rate limiter.
- Public GitHub repository cloning uses unauthenticated HTTPS only and bounded file snapshots. Very large repositories may time out or be truncated.
- Never automatically apply model output, run a generated shell command, commit, push, or open a pull request.
- Repository contents are untrusted input and may contain prompt injection. The AI prompt treats file contents as data, and returned file paths are checked against the scanned manifest. Model output remains advisory.
- The public deployment has no durable session database. Sessions can disappear on restart or sleep. Keep one running instance for the demo.
- The service has not been penetration-tested. Do not treat it as a secure sandbox or as suitable for sensitive code without a security review.
- A successful compile/test is not proof of correctness, security, or maintainability.

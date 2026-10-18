# Architecture

- **Next.js client:** maximalist contribution workspace, source evidence, artifact previews and verification ledger.
- **Next.js API rewrites:** local development calls `http://localhost:8000`; the single-container public deployment proxies same-origin `/api/*` requests to FastAPI on loopback.
- **FastAPI:** request validation, provider selection, repository analysis orchestration and in-memory session mapping.
- **Gemma API adapter:** default hosted provider uses the Google Gemini API endpoint with the Gemma 4 model `gemma-4-26b-a4b-it`. The API key is read only from server-side `GEMINI_API_KEY`. A local Ollama provider remains available by setting `PATCHTRACE_AI_PROVIDER=ollama`.
- **Repository inspector:** bounded file snapshot, manifest/language detection, line-level excerpts and heuristic candidate ranking. Suggested file paths from the model are allowlisted against the scanned manifest.
- **Artifact builder:** Markdown contribution brief and a tightly constrained demo README correction diff. Proposed changes are never written back to the source repository.
- **Check runner:** Python compilation can be explicitly triggered; tests are a separate, gated check. Public mode disables arbitrary pytest execution.
- **Render Docker service:** one public URL serves both UI and API; Render injects `GEMINI_API_KEY` as a secret environment variable.

## Session scope

Repository snapshots live below `backend/.patchtrace/workspaces/`; result state is in process memory and sessions are bounded. Sessions are not durable and disappear when the backend restarts or the hosting instance sleeps. The public demo is intended for a small, single-instance demonstration, not scaled multi-tenant production.

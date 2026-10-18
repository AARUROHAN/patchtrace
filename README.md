# PatchTrace

**Evidence-backed first contributions. Less AI theatre. More proof.**

PatchTrace is a PS04 prototype for Hacktoberfest HackDay 2026 (Track 2: Best Open-Source AI Project). It inspects a public or local repository, connects a development task to repository evidence, generates a reviewable contribution artifact, and records checks as passed, failed, or not run.

> **Status:** MVP / HackDay prototype, not production-hardened. GitHub Actions now runs backend tests, frontend TypeScript checks, and a Next.js production build. Manual Render testing has produced live Gemma 4 responses, but model reliability and recommendation quality across different repositories are not yet benchmarked. All AI findings are advisory and require human review.

## Product idea

PatchTrace creates a verifiable contribution trail:

1. **Inspect** repository structure, languages, entry points, documentation, manifests and test surfaces.
2. **Trace** a task to relevant files and line-numbered evidence. AI-suggested paths are filtered against the files actually scanned.
3. **Draft** an issue-linked contribution brief and, for the synthetic demo's stale README command, a proposed unified diff.
4. **Verify** available checks with an execution ledger. A planned command is never shown as a test result.

PatchTrace never applies a proposed diff automatically and does not create commits or pull requests.

## Technology stack

- **Frontend:** Next.js, React, TypeScript, custom CSS.
- **Backend:** Python, FastAPI, Pydantic.
- **AI:** Google Gemma 4 API using `gemma-4-26b-a4b-it` by default; optional local Ollama provider for local development.
- **Repository inspection:** bounded text/code snapshot, heuristic ranking, line-level evidence extraction.
- **Verification:** explicitly triggered checks and a status ledger.

Official Gemma API guide: <https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api>. The current hosted Gemma API model IDs documented by Google include `gemma-4-26b-a4b-it` and `gemma-4-31b-it`.

## Quick start on macOS

Requirements: macOS, Python 3.11+, Node.js 20.9+, npm, and Git for public GitHub repositories.

```bash
./scripts/setup_macos.sh
```

The setup script creates `backend/.venv`, installs Python/frontend dependencies, and copies `backend/.env.example` to `backend/.env` if no local env file exists.

### Configure Gemma API locally

1. Create a key in [Google AI Studio](https://aistudio.google.com/apikey).
2. Open `backend/.env` in VS Code or a text editor and set:

   ```env
   PATCHTRACE_AI_PROVIDER=gemma_api
   GEMINI_API_KEY=PASTE_YOUR_PRIVATE_KEY_HERE
   GEMMA_API_MODEL=gemma-4-26b-a4b-it
   PATCHTRACE_PUBLIC_MODE=false
   ```

3. Save the file. Keep it private; `.env` files are gitignored.
4. Run a real small API check:

   ```bash
   ./scripts/smoke_gemma_api.sh
   ```

5. Start the application:

   ```bash
   ./scripts/start_macos.sh
   ```

6. Open <http://localhost:3000>. API health: <http://localhost:8000/api/health>; interactive API docs: <http://localhost:8000/api/docs>.

If you have no API key, the demo and deterministic inspection still work; the AI section will explain why Gemma did not respond. When the AI toggle is enabled, PatchTrace sends the task, repository overview, selected file paths, and bounded evidence snippets to Google's Gemma API. Do not send private or secret-bearing code unless you have permission to do so.

### Optional local Ollama instead

Ollama is a local alternative, not the hosted API mode described above. Change `PATCHTRACE_AI_PROVIDER=ollama` and configure `PATCHTRACE_OLLAMA_URL` and `PATCHTRACE_OLLAMA_MODEL` in `backend/.env`. Then restart the backend. Gemma 4 model tags and hardware requirements can change; select a tag your machine can run.

## Publish it publicly with Render

This repository includes a single-service Docker deployment and `render.yaml`. The container runs Next.js and FastAPI together; the browser calls same-origin `/api/*` routes, and Next.js proxies those requests internally to FastAPI. This avoids exposing the API key in frontend code and avoids cross-origin setup for the public demo.

1. **Publish the project to a public GitHub repository** named `patchtrace` (or another name you choose). Push the project root, including `Dockerfile` and `render.yaml`. Do not upload `backend/.env` or any API key.
2. Go to <https://dashboard.render.com/> and choose **New → Blueprint**. Connect the GitHub repository containing `render.yaml`.
3. Review the single `patchtrace-gemma` web service. Render will prompt for the secret `GEMINI_API_KEY`; enter your Google AI Studio key in the Render form only.
4. Deploy and wait for the build to complete. Open the public URL shown in Render.
5. Check `/api/health` on that public URL. It should return `status: ok`, `mode: public-demo`, and `model.configured: true` when the API key is set.
6. In the live interface, inspect the included synthetic demo with **ASK GEMMA 4** enabled. Then inspect a public, openly licensed repository and compare the evidence/plan with the actual source.

Current Render instructions: <https://render.com/docs/your-first-deploy> and <https://render.com/docs/blueprint-spec>.

**Hosting notes:** This is configured as a small public demo, not a hardened multi-tenant production system. Render's free instances can sleep and cold-start. Sessions and inspected snapshots are in memory/ephemeral storage; keep one running instance and re-inspect if a session is lost. The global per-instance model-request throttle is intentionally conservative. Review current Render plan limits and Google AI Studio quota/pricing before sharing widely.

## Public-mode safeguards

The Docker/Render configuration enables `PATCHTRACE_PUBLIC_MODE=true`:

- Local filesystem paths are disabled; the server can only inspect its bundled demo or public GitHub repository URLs.
- Running arbitrary repository `pytest` suites is disabled on the public service because tests may execute arbitrary code. The interface records them as not run. A Python syntax compilation check may be explicitly run.
- Proposed diffs are never auto-applied.
- Source snapshots are bounded by file count/size limits; `.git`, virtual environments, node_modules and common generated directories are skipped.
- The public demo rate-limits POST requests within the single service process. It is a lightweight safeguard, not a substitute for a managed WAF or production-grade distributed rate limiter.
- Sessions are memory-only and limited; they may disappear on a restart or if the service sleeps.

Use a disposable container or VM to run tests from untrusted repositories locally. This project has not been penetration-tested and should not be used to process secret or private code without a security review.

## API overview

- `GET /api/health` — service/model configuration (never returns API-key values).
- `GET /api/demo` — clearly labelled synthetic demo information.
- `POST /api/inspect` — inspect a repository and task; optionally call Gemma 4.
- `POST /api/checks/{repo_id}` — execute one supported check, subject to public-mode restrictions.
- `GET /api/docs` — interactive API docs.

Example request:

```json
{
  "source_type": "demo",
  "source": "",
  "task": "The README quick-start command references a script that is missing. Propose a reviewable docs correction.",
  "use_ai": true
}
```

For GitHub use `source_type: "github"` and `source: "https://github.com/OWNER/REPO"`. For a local development run only, `source_type: "local"` accepts a path on the backend machine.

## Tests and verification status

Run backend tests locally:

```bash
cd backend
python -m pip install -r requirements.txt
python -m pytest -q tests
```

The GitHub Actions workflow at `.github/workflows/tests.yml` checks:
- Backend test suite, including API request validation, safety restrictions, evidence extraction, artifact generation and mocked Gemma response handling.
- Frontend TypeScript type-checking.
- Next.js production build.

The mocked AI tests do not prove that Google AI Studio is reachable or that a live response will always follow the requested format. To test current live model access, configure `GEMINI_API_KEY` in `backend/.env` and run `./scripts/smoke_gemma_api.sh`; never commit the key.

Manual Render testing has produced Gemma responses, including a cautious JUCE repository exploration result. That confirms a live response was received at least once; it does **not** certify the suggested issue, prove repository-wide coverage, or measure accuracy. Large monorepos are scanned with bounded file/size limits, and the system may correctly report insufficient evidence.

Before treating a contribution as actionable, inspect the cited source context, reproduce the problem where possible, and run tests in a disposable environment when repository code is untrusted. A status of `RESPONDED` means the model returned a usable answer; it does not mean a bug was verified or a fix passed.

## Official PS04 mapping

| Requirement | PatchTrace evidence |
|---|---|
| Show repository languages, entry points, contributor guidance and relevant directories | Repository overview panel |
| Accept an issue/task and cite relevant files | Task input, ranked files, path/line evidence |
| Produce a reviewable contribution artifact | Downloadable Markdown brief and the demo's proposed README diff |
| Run a useful check and state passed/failed/not run | Explicit verification ledger |
| Explain model role and access/coverage limits | Gemma provider status, evidence-bounded paths and coverage warning |
| Keep open-weight AI meaningful | Gemma 4 API interprets the task and proposes the investigation plan; deterministic code validates paths and records real check outcomes |
| Publish source and reproducible instructions | MIT licence, this README, synthetic demo repository and tests |

## License

MIT — see [LICENSE](LICENSE). The included demo repository is synthetic and is separately labelled within the project.

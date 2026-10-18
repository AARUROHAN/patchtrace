# PatchTrace Demo CLI

> **Synthetic demo repository.** Created only to demonstrate repository evidence and documentation repair; it is not a real third-party open-source project.

A tiny Python CLI used in the PatchTrace PS04 walkthrough.

## Quick start

Create a virtual environment and install development dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Run the CLI help command:

```bash
python app.py --help
```

The command above is intentionally stale: there is no `app.py` at the repository root. The CLI module lives under `src/patchtrace/cli.py`. This seeded defect makes the demo repeatable.

## Tests

```bash
python -m pytest -q
```

## Development notes

- Entry point: `src/patchtrace/cli.py`
- Tests: `tests/test_cli.py`
- License: MIT

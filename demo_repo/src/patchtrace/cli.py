"""A tiny CLI with a deterministic help output for the PatchTrace demo."""
from __future__ import annotations
import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="patchtrace-demo", description="Synthetic CLI for a repository-analysis walkthrough.")
    parser.add_argument("--version", action="version", version="patchtrace-demo 0.1.0")
    parser.add_argument("--check", action="store_true", help="Print a deterministic health message.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check:
        print("patchtrace-demo: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

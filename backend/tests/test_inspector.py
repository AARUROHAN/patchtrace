from pathlib import Path
from app.services.repo_inspector import DEMO_REPO, _discovery_evidence, inspect_repository, prepare_repository


def test_demo_inspection_finds_readme_and_python():
    repo_id, root, label = prepare_repository("demo", "")
    try:
        result = inspect_repository(root, repo_id, label, "README quick start command missing app.py")
        overview = result["overview"]
        assert overview["file_count"] >= 4
        assert "Python" in [x["name"] for x in overview["languages"]]
        assert "README.md" in overview["readme_files"]
        assert "src/patchtrace/cli.py" in overview["entry_points"]
        assert "LICENSE" in overview["license_files"]
        assert "tests" in overview["test_directories"]
        assert any(item["path"] == "README.md" for item in result["candidates"])
        assert any(item["path"] == "README.md" and item["line"] > 0 for item in result["evidence"])
    finally:
        import shutil
        shutil.rmtree(root.parent, ignore_errors=True)


def test_local_source_requires_existing_directory(tmp_path):
    try:
        prepare_repository("local", str(tmp_path / "missing"))
    except ValueError as exc:
        assert "existing repository directory" in str(exc)
    else:
        raise AssertionError("Expected invalid local path to fail")


def test_github_source_rejects_non_github_url():
    try:
        prepare_repository("github", "https://example.com/owner/repo")
    except ValueError as exc:
        assert "https://github.com" in str(exc)
    else:
        raise AssertionError("Expected a non-GitHub URL to fail")



def test_demo_quickstart_evidence_includes_command_and_stale_explanation():
    repo_id, root, label = prepare_repository("demo", "")
    try:
        task = (
            "Inspect the synthetic repository and identify the documented quick-start command. "
            "Use actual files and evidence to explain the issue and propose a documentation-only correction."
        )
        result = inspect_repository(root, repo_id, label, task)
        readme_evidence = [item for item in result["evidence"] if item["path"] == "README.md"]
        texts = [item["text"].lower() for item in readme_evidence]
        assert any("python app.py --help" in line for line in texts)
        assert any("intentionally stale" in line or "does not exist" in line for line in texts)
    finally:
        import shutil
        shutil.rmtree(root.parent, ignore_errors=True)



def test_snapshot_prioritises_native_source_and_preserves_case(tmp_path):
    from app.services.repo_inspector import _copy_local_snapshot

    source = tmp_path / "source"
    destination = tmp_path / "snapshot"
    (source / "modules" / "juce_core").mkdir(parents=True)
    (source / "docs").mkdir()
    (source / "README.md").write_text("# Project\n", encoding="utf-8")
    (source / "LICENSE").write_text("Example licence\n", encoding="utf-8")
    (source / "modules" / "juce_core" / "juce_core.cpp").write_text("int core() { return 1; }\n", encoding="utf-8")
    (source / "modules" / "juce_core" / "juce_core.h").write_text("int core();\n", encoding="utf-8")
    (source / "docs" / "guide.md").write_text("# Guide\n" + ("Documentation.\n" * 20), encoding="utf-8")

    _copy_local_snapshot(source, destination)

    assert (destination / "README.md").exists()
    assert (destination / "LICENSE").exists()
    assert (destination / "modules" / "juce_core" / "juce_core.cpp").exists()
    assert (destination / "modules" / "juce_core" / "juce_core.h").exists()



def test_exploratory_review_uses_signal_evidence_not_generic_prompt_keywords():
    repo_id, root, label = prepare_repository("demo", "")
    try:
        task = (
            "Explore this repository to identify the single most promising, evidence-backed "
            "contribution opportunity for a new open-source contributor. Inspect the README, "
            "setup instructions, source files, project configuration, and tests. Look for "
            "verifiable documentation problems, missing tests, potentially broken workflows, "
            "or clearly supported bugs. Do not invent findings."
        )
        result = inspect_repository(root, repo_id, label, task)
        assert result["candidates"]
        assert any(item["path"] == "README.md" for item in result["candidates"])
        assert any(
            item["path"] == "README.md"
            and ("intentionally stale" in item["text"].lower() or "does not exist" in item["text"].lower())
            for item in result["evidence"]
        )
        assert all(item["path"] != "LICENSE" for item in result["evidence"])
        assert result["task_tokens"] == sorted({
            "todo", "fixme", "bug", "deprecated", "obsolete",
            "xxx", "hack", "workaround", "stale", "implemented", "exists",
        })
    finally:
        import shutil
        shutil.rmtree(root.parent, ignore_errors=True)



def test_discovery_evidence_skips_platform_guards_and_runtime_messages(tmp_path):
    source = tmp_path / "module.cpp"
    source.write_text(
        '#error "SIMD register support not implemented for this platform"\\n'
        'const char* message = "Requested client does not exist";\\n'
        '// TODO: Add regression coverage for this fallback path.\\n',
        encoding="utf-8",
    )
    readme = tmp_path / "README.md"
    readme.write_text(
        "# Quick start\\nThe documented launcher does not exist in the current checkout.\\n",
        encoding="utf-8",
    )

    evidence = _discovery_evidence(tmp_path, ["module.cpp", "README.md"])
    evidence_text = [item["text"] for item in evidence]

    assert not any("SIMD register support" in item for item in evidence_text)
    assert not any("Requested client does not exist" in item for item in evidence_text)
    todo = next(item for item in evidence if "TODO:" in item["text"])
    assert todo["line"] == 3
    assert "2: const char* message" in todo["context"]
    assert "3: // TODO:" in todo["context"]
    assert any(
        item["path"] == "README.md" and "does not exist" in item["text"]
        for item in evidence
    )

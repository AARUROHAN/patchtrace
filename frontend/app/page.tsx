"use client";

import { useCallback, useEffect, useState } from "react";

type Check = { id: string; label: string; command: string; type: string; status: string; detail: string; can_run?: boolean; output?: string; return_code?: number | null };
type Evidence = { path: string; line: number; text: string; context?: string; relevance: number };
type Candidate = { path: string; score: number; reason: string };
type GitHubIssue = { number: number; title: string; body: string; url: string; state: string; labels: string[]; comments: number };
type Result = {
  repo_id: string;
  task: string;
  issue?: GitHubIssue | null;
  issue_candidates?: GitHubIssue[];
  issue_search_error?: string | null;
  overview: {
    source_label: string; file_count: number; bytes_scanned: number; truncated: boolean;
    languages: { name: string; count: number }[]; manifest_files: string[]; readme_files: string[]; license_files: string[];
    entry_points: string[]; test_directories: string[]; contributor_instructions: string[]; ci_files: string[]; directories: string[];
  };
  candidates: Candidate[]; evidence: Evidence[]; checks: Check[]; artifact: string;
  proposed_diff: { path: string; diff: string; status: string } | null;
  ai: { enabled: boolean; used: boolean; configured: boolean; provider?: string; model: string | null; error?: string | null; format_warning?: string | null; summary: string; plan: string[]; questions: string[]; risks: string[]; notice: string };
  coverage: { status: string; files_scanned: number; bytes_scanned: number; warning: string };
};

type Health = { status: string; public_mode?: boolean; model: { configured: boolean; model: string | null; provider: string; provider_id?: string } };
const PUBLIC_MODE = process.env.NEXT_PUBLIC_PATCHTRACE_PUBLIC_MODE === "true";
const API = process.env.NEXT_PUBLIC_PATCHTRACE_API || (PUBLIC_MODE ? "" : "http://localhost:8000");
const DEFAULT_TASK = "The README quick-start command references a script that is missing. Propose a reviewable documentation correction without changing application logic.";
const EXPLORE_TASK = "Explore this repository to identify one promising, evidence-backed contribution opportunity for a new open-source contributor. Prioritise current source, tests, and reproducible documentation inconsistencies. Return one candidate with exact file paths and line-level evidence, a minimal proposed change, verification steps, risks, and questions. Do not treat historical release notes as proof of a current bug or invent findings. If evidence is insufficient, explain what should be inspected next."; 
const ISSUE_TASK = "Explain this issue in simple language. Identify what we should investigate in the source, suggest one small contribution, and name a relevant test or verification step. Separate confirmed facts from guesses; do not invent bugs or test results."; 
function Icon({ children }: { children: React.ReactNode }) { return <span className="glyph" aria-hidden="true">{children}</span>; }
function Status({ value }: { value: string }) {
  const cls = value === "passed" ? "pass" : value === "failed" ? "fail" : value === "not_run" ? "not-run" : "neutral";
  const label = value === "not_run" ? "NOT RUN" : value.toUpperCase().replaceAll("_", " ");
  return <span className={`status ${cls}`}><span className="status-dot" />{label}</span>;
}
function formatBytes(n: number) { return n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / (1024 * 1024)).toFixed(2)} MB`; }

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [sourceType, setSourceType] = useState<"demo" | "github" | "local">("demo");
  const [source, setSource] = useState("");
  const [workflowMode, setWorkflowMode] = useState<"explore" | "issue">("issue");
  const [task, setTask] = useState(DEFAULT_TASK);
  const [useAI, setUseAI] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [busyCheck, setBusyCheck] = useState("");
  const [error, setError] = useState("");
  const [activeTab, setActiveTab] = useState<"evidence" | "artifact" | "verification">("evidence");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    fetch(`${API}/api/health`).then(r => r.json()).then((d: Health) => {
      setHealth(d);
      setUseAI(Boolean(d?.model?.configured));
    }).catch(() => setHealth(null));
  }, []);

  const inspect = useCallback(async (override?: { type: "demo" | "github" | "local"; source: string; task: string }) => {
    const input = override ?? { type: sourceType, source, task };
    setBusy(true); setError(""); setResult(null); setActiveTab("evidence");
    try {
      const response = await fetch(`${API}/api/inspect`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_type: input.type, source: input.source, task: input.task, use_ai: useAI }),
      });
      const responseText = await response.text();
      let data: any;
      try {
        data = responseText ? JSON.parse(responseText) : {};
      } catch {
        const preview = responseText.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim().slice(0, 350);
        throw new Error(`Server returned a non-JSON response (HTTP ${response.status}). ${preview || response.statusText}`);
      }
      if (!response.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `Inspection failed (${response.status})`);
      setResult(data as Result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not connect to PatchTrace API. Is the backend running?");
    } finally { setBusy(false); }
  }, [sourceType, source, task, useAI]);

  async function runCheck(check: Check) {
    if (!result) return;
    let confirmed = false;
    if (check.id === "pytest") {
      confirmed = window.confirm("Tests execute code from the selected repository. Run only if you trust this repository. For unknown code, use a disposable container or VM. Continue?");
      if (!confirmed) return;
    }
    setBusyCheck(check.id); setError("");
    try {
      const response = await fetch(`${API}/api/checks/${result.repo_id}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ check_id: check.id, confirm_execution: confirmed }),
      });
      const responseText = await response.text();
      let data: any;
      try {
        data = responseText ? JSON.parse(responseText) : {};
      } catch {
        const preview = responseText.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim().slice(0, 350);
        throw new Error(`Server returned a non-JSON response (HTTP ${response.status}). ${preview || response.statusText}`);
      }
      if (!response.ok) throw new Error(typeof data?.detail === "string" ? data.detail : "Check failed to start");
      setResult(current => current ? { ...current, checks: current.checks.map(c => c.id === check.id ? { ...c, ...data } : c) } : current);
      setActiveTab("verification");
    } catch (e) { setError(e instanceof Error ? e.message : "Check could not be run."); }
    finally { setBusyCheck(""); }
  }

  function download(name: string, content: string) {
    const url = URL.createObjectURL(new Blob([content], { type: "text/plain;charset=utf-8" }));
    const a = document.createElement("a"); a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  }
  async function copyArtifact() {
    if (!result) return;
    try { await navigator.clipboard.writeText(result.artifact); setCopied(true); window.setTimeout(() => setCopied(false), 1600); }
    catch { setError("Clipboard permission was blocked. Use Download brief instead."); }
  }

  return <main className="site-shell">
    <div className="grain" aria-hidden="true" />
    <header className="topnav">
      <a className="brand" href="#top" aria-label="PatchTrace home"><span className="brand-mark">P<span>↗</span></span><span>patch<span className="brand-accent">trace</span></span></a>
      <nav className="nav-links"><a href="#workflow">WORKFLOW</a><a href="#workspace">WORKSPACE</a><a href="#proof">PROOF RULES</a></nav>
    </header>

    <section id="top" className="hero">
      <div className="hero-copy">
        <div className="eyebrow"><span className="eyebrow-line" /> OPEN-SOURCE ENGINEERING, WITH RECEIPTS <span className="eyebrow-star">✳</span></div>
        <h1>DON&apos;T JUST<br /><span className="headline-stroke">SHIP AI.</span><br /><span className="headline-highlight">SHOW PROOF.</span></h1>
        <p className="hero-description">Turn an unfamiliar repository and one real issue into a <strong>traceable contribution path</strong> — relevant files, line-level evidence, a reviewable artifact, and checks that tell the truth.</p>
        <div className="hero-actions"><button className="button-primary" onClick={() => { setSourceType("demo"); setSource(""); setWorkflowMode("issue"); setTask(DEFAULT_TASK); void inspect({ type: "demo", source: "", task: DEFAULT_TASK }); }} disabled={busy}><span>{busy ? "MAPPING REPOSITORY…" : "INSPECT THE DEMO REPO"}</span><span className="button-arrow">↗</span></button><a className="text-link" href="#workflow">See the proof trail <span>↓</span></a></div>
      </div>
      <div className="hero-art" aria-label="Illustration of evidence connected to a code change">
        <div className="art-grid" />
        <div className="orbit orbit-one" /><div className="orbit orbit-two" />
        <div className="art-sticker sticker-top">NO VIBES.<br />JUST EVIDENCE.</div>
        <div className="art-card code-card"><div className="code-card-top"><span className="window-dots"><i /><i /><i /></span><span>README.md</span><span className="line-ref">L. 18</span></div><div className="code-lines"><span className="line-no">16</span><span className="code-muted"># Quick start</span><span className="line-no">17</span><span>Install the package</span><span className="line-no line-bad">18</span><span className="code-bad">$ python app.py --help</span><span className="line-no">19</span><span className="code-muted"># Where&apos;s app.py?</span></div><div className="code-card-footer"><span className="evidence-dot" /> SOURCE EVIDENCE FOUND <span className="footer-arrow">↘</span></div></div>
        <div className="art-card patch-card"><div className="patch-card-top"><span>PROPOSED PATCH</span><span className="patch-star">✳</span></div><div className="diff-row diff-remove"><b>−</b> python app.py --help</div><div className="diff-row diff-add"><b>+</b> PYTHONPATH=src python -m patchtrace.cli --help</div><div className="patch-card-bottom"><span className="tiny-shield">✓</span> PROPOSED · NOT APPLIED</div></div>
        <div className="art-sticker sticker-bottom">TRUST<br /><span>IS A FEATURE.</span></div>
        <div className="floating-stamp">TRACE<br /><b>01—04</b></div>
        <svg className="connector connector-a" viewBox="0 0 160 100" aria-hidden="true"><path d="M5 8 C70 4 40 74 145 83" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="4 5"/><circle cx="145" cy="83" r="4" fill="currentColor"/></svg>
      </div>
    </section>

    <section id="workflow" className="workflow-strip">
      <div className="section-mini-title"><span>THE METHOD</span><span className="section-rule" /><span>FOUR STEPS. ZERO MAGIC CLAIMS.</span></div>
      <div className="workflow-grid">
        <div className="workflow-step"><span className="step-number">01</span><span className="step-icon icon-lime">⌘</span><div><h3>INSPECT</h3><p>Map languages, entry points, docs and test surfaces.</p></div><span className="step-arrow">↗</span></div>
        <div className="workflow-step"><span className="step-number">02</span><span className="step-icon icon-lilac">⌕</span><div><h3>TRACE</h3><p>Connect the issue to exact paths and numbered lines.</p></div><span className="step-arrow">↗</span></div>
        <div className="workflow-step"><span className="step-number">03</span><span className="step-icon icon-coral">✎</span><div><h3>PROPOSE</h3><p>Generate a bounded artifact a maintainer can review.</p></div><span className="step-arrow">↗</span></div>
        <div className="workflow-step"><span className="step-number">04</span><span className="step-icon icon-blue">✓</span><div><h3>VERIFY</h3><p>Record what ran, what failed, and what did not run.</p></div><span className="step-arrow">↗</span></div>
      </div>
    </section>

    <section id="workspace" className="workspace-section">
      <div className="workspace-heading"><h2>Make your <span>first move.</span></h2></div>
      <div className="workbench-grid">
        <div className="input-card">
          <h3>Give us the context.</h3><p className="muted-copy">Use the labelled synthetic demo or a public GitHub repository. Only scan code you have permission to share.</p>
          <div className="field-label">REPOSITORY SOURCE</div>
          <div className="source-tabs" role="group" aria-label="Repository source type">
            <button className={sourceType === "demo" ? "selected" : ""} onClick={() => { setSourceType("demo"); setSource(""); }}>✳ Demo repo</button>
            <button className={sourceType === "github" ? "selected" : ""} onClick={() => setSourceType("github")}>↗ GitHub URL</button>
            {!PUBLIC_MODE && <button className={sourceType === "local" ? "selected" : ""} onClick={() => setSourceType("local")}>⌂ Local path</button>}
          </div>
          {sourceType !== "demo" && <label className="input-wrap"><span>{sourceType === "github" ? "PUBLIC REPOSITORY OR ISSUE URL" : "LOCAL REPOSITORY DIRECTORY PATH"}</span><input value={source} onChange={e => setSource(e.target.value)} placeholder={sourceType === "github" ? "https://github.com/owner/repo/issues/123 (or repository URL)" : "/Users/you/code/project"} autoComplete="off" /></label>}
          <div className="field-label">WHAT DO YOU WANT TO DO?</div>
          <div className="source-tabs workflow-tabs" role="group" aria-label="Contribution workflow">
            <button type="button" aria-pressed={workflowMode === "explore"} className={workflowMode === "explore" ? "selected" : ""} onClick={() => { setWorkflowMode("explore"); setTask(EXPLORE_TASK); }}>⌕ Explore opportunities</button>
            <button type="button" aria-pressed={workflowMode === "issue"} className={workflowMode === "issue" ? "selected" : ""} onClick={() => { setWorkflowMode("issue"); setTask(ISSUE_TASK); }}>↗ Investigate an issue</button>
          </div>
          <label className="input-wrap task-field"><span>{workflowMode === "explore" ? "REPOSITORY REVIEW GOAL" : "ISSUE / TASK"} <span className="required-mark">REQUIRED</span></span><textarea value={task} onChange={e => setTask(e.target.value)} rows={4} maxLength={3000} placeholder={workflowMode === "explore" ? "Explore the repository for one evidence-backed contribution opportunity…" : "Paste a GitHub issue, bug report, or documentation task…"} /></label>
          <div className="ai-control"><div><span className="ai-icon">✳</span><div><b>ASK GEMMA 4</b><small>{health?.model?.configured ? `Configured · ${health.model.model} · live request unverified` : "Not configured · API key required on the server"}</small></div></div><button className={`switch ${useAI ? "on" : ""}`} role="switch" aria-checked={useAI} aria-label="Use Gemma 4 API" onClick={() => setUseAI(v => !v)}><span /></button></div>
          <div className="input-disclaimer"><span>ⓘ</span><p>{useAI ? "Gemma API is enabled: the task plus selected repository summary and snippets are sent to Google for model analysis. Do not submit private or secret-bearing code." : "AI is off for this run. Patches are suggestions; nothing is applied automatically."}</p></div>
          {error && <div className="error-callout" role="alert"><b>Something needs attention</b><span>{error}</span></div>}
          <button className="button-dark" onClick={() => void inspect()} disabled={busy || task.trim().length < 12 || (sourceType !== "demo" && !source.trim())}><span>{busy ? "BUILDING PROOF TRAIL…" : "ANALYSE REPOSITORY"}</span><span>↗</span></button>
          {busy && <div className="button-subtext">Scanning bounded text files and preparing evidence.</div>}
        </div>

        <div className="results-column">
          <div className="result-banner"><div><h3>{result ? "The trail is mapped." : "Proof starts at the source."}</h3><p>{result ? result.overview.source_label : "Run an inspection to populate the evidence, artifact and verification panels."}</p></div><div className={`scanner-mark ${busy ? "scanning" : ""}`}><span>{busy ? "…" : result ? "✓" : "↘"}</span><small>{busy ? "SCANNING" : result ? "MAPPED" : "READY"}</small></div></div>
          {result && <section className="contribution-summary" aria-labelledby="contribution-summary-title">
            <div className="contribution-summary-head">
              <div>
                <span className="summary-kicker">YOUR CONTRIBUTION SUMMARY</span>
                <h3 id="contribution-summary-title">{result.issue ? ("Issue #" + result.issue.number + ": " + result.issue.title) : "What should we work on?"}</h3>
                <p>{result.issue ? <a href={result.issue.url} target="_blank" rel="noreferrer">Open original GitHub issue ↗</a> : result.overview.source_label}</p>
              </div>
              {result.issue && <span className={result.issue.state === "open" ? "issue-state open" : "issue-state"}>{result.issue.state === "open" ? "OPEN ISSUE" : result.issue.state.toUpperCase()}</span>}
            </div>
            <div className="summary-repo-guide">
              <span className="summary-kicker">QUICK REPOSITORY GUIDE</span>
              <p><b>Languages:</b> {result.overview.languages.map(lang => lang.name).join(", ") || "Not identified"}</p>
              <p><b>Architecture hints:</b> {result.overview.manifest_files.slice(0, 3).join(", ") || "No project manifest identified"}</p>
              <p><b>Entry points:</b> {result.overview.entry_points.slice(0, 3).join(", ") || "Not identified"} <span className="guide-separator">·</span> <b>Test setup:</b> {result.overview.test_directories.slice(0, 3).join(", ") || "No test directory identified"}</p>
            </div>
            <div className="summary-problem">
              <span>THE PROBLEM</span>
              <p>{result.issue ? (result.issue.body?.trim() || result.issue.title).slice(0, 720) : result.task}</p>
            </div>
            <div className="summary-grid">
              <article>
                <h4>What we found</h4>
                <p>{result.ai.used ? result.ai.summary : result.candidates[0] ? ("The scan found a place worth reviewing: " + result.candidates[0].path + ". " + result.candidates[0].reason) : "We haven't found enough evidence to describe a likely cause yet. Review the source before changing anything."}</p>
              </article>
              <article>
                <h4>What to investigate</h4>
                <ol>{(result.ai.plan?.length ? result.ai.plan.slice(0, 3) : ["Read the issue and confirm the behaviour.", "Inspect the most relevant source and related tests.", "Reproduce the problem before choosing a fix."]).map((step, i) => <li key={i}>{step}</li>)}</ol>
              </article>
              <article>
                <h4>What we can contribute</h4>
                <p>{result.proposed_diff ? ("A small documentation change is proposed in " + result.proposed_diff.path + ". It is only a draft and has not been applied.") : result.candidates[0] ? ("Start by checking " + result.candidates[0].path + ". If the issue is confirmed, contribute a focused fix or regression test rather than changing unrelated code.") : "No safe code change is clear yet. Confirm the cause first, then choose the smallest fix or focused test."}</p>
              </article>
            </div>
            <div className="summary-followup-grid">
              <article className="summary-followup">
                <h4>Files to investigate</h4>
                {result.candidates.length ? <ul className="summary-file-list">{result.candidates.slice(0, 3).map(candidate => <li key={candidate.path}><code>{candidate.path}</code><span>{candidate.reason}</span></li>)}</ul> : <p>No source file could be prioritised. Review the repository structure and issue manually before editing.</p>}
              </article>
              <article className="summary-followup">
                <h4>Regression test</h4>
                <p>Add or update a focused test that reproduces the reported behaviour and fails before the fix. {result.overview.test_directories.length ? "Existing test area: " + result.overview.test_directories.slice(0, 2).join(", ") + "." : "No test directory was identified; check the project's documented test setup first."}</p>
                <div className="summary-check-list">
                  <b>Suggested commands</b>
                  {result.checks.filter(check => check.id === "pytest" || check.id === "python_compile" || check.type === "suggested-command").slice(0, 3).map(check => <div key={check.id} className="summary-check">
                    <code>{check.command}</code>
                    <span className={check.status === "passed" ? "check-state passed" : check.status === "failed" ? "check-state failed" : "check-state"}>{check.status === "not_run" ? "NOT RUN" : check.status.toUpperCase().replaceAll("_", " ")}</span>
                    <small>{check.detail}</small>
                  </div>)}
                  {!result.checks.some(check => check.id === "pytest" || check.id === "python_compile" || check.type === "suggested-command") && <p>No reliable test command could be inferred. Check the README and project manifest for the maintainer's instructions.</p>}
                </div>
              </article>
            </div>
            {result.evidence?.[0] && <div className="summary-evidence">
              <span>ONE SOURCE TO CHECK</span><code>{result.evidence[0].path}:L{result.evidence[0].line}</code><p>{result.evidence[0].text}</p>
            </div>}
            <p className="summary-note"><b>Confidence & limits:</b> {result.evidence.length ? "Provisional — source lines were found, but the reported problem still needs to be reproduced or verified with a relevant check." : "Low — no matching line-level evidence was returned. Investigate the source manually before proposing a fix."} AI suggestions are advisory; a response does not prove the issue or fix is correct.</p>
            {!result.ai.used && <p className="summary-note">AI analysis was not available for this run. Findings above use repository inspection and are not confirmed fixes.</p>}
          </section>}
          {Boolean(result?.issue_candidates?.length) && result && <section className="issue-shortlist" aria-labelledby="issue-shortlist-title">
            <div><span className="summary-kicker">GITHUB ISSUE FINDER</span><h3 id="issue-shortlist-title">Choose an open issue to investigate</h3><p>These are open issues from this repository. Pick one and PatchTrace will load its description and inspect the code for that specific problem.</p></div>
            <div className="issue-shortlist-list">{result.issue_candidates?.slice(0, 10).map(issue => <article className="issue-shortlist-item" key={issue.number}>
              <div><b>#{issue.number} · {issue.title}</b><p>{issue.body?.trim() ? issue.body.trim().slice(0, 200) : "No description provided."}</p>{issue.labels?.length > 0 && <small>{issue.labels.slice(0, 3).join(" · ")}</small>}</div>
              <button type="button" onClick={() => { const nextTask = "Explain this issue in simple language. Identify what we should investigate in the source, suggest one small contribution, and name a relevant test or verification step. Separate confirmed facts from guesses."; setSourceType("github"); setSource(issue.url); setWorkflowMode("issue"); setTask(nextTask); void inspect({ type: "github", source: issue.url, task: nextTask }); }}>Investigate issue ↗</button>
            </article>)}</div>
          </section>}
          {result?.issue_search_error && !result.issue && <p className="summary-note">{result.issue_search_error}</p>}
          {result && sourceType === "github" && !result.issue && !result.issue_candidates?.length && !result.issue_search_error && <p className="summary-note">No open issues were returned for this repository. Paste a direct issue URL or try another repository.</p>}
          {result ? <>
            <details className="technical-details"><summary>Technical details (optional)</summary>
            <div className="repo-overview-panel"><div className="panel-title"><span>REPOSITORY SNAPSHOT</span><span className={`scan-tag ${result.coverage.status}`}>{result.coverage.status.toUpperCase()} COVERAGE</span></div><div className="language-row">{result.overview.languages.map(lang => <span key={lang.name} className="language-chip"><i />{lang.name}<b>{lang.count}</b></span>)}</div><div className="repo-meta-grid"><div><small>ENTRY POINTS</small><b>{result.overview.entry_points.join(", ") || "Not found"}</b></div><div><small>MANIFESTS</small><b>{result.overview.manifest_files.join(", ") || "Not found"}</b></div><div><small>TEST SURFACES</small><b>{result.overview.test_directories.join(", ") || "Not identified"}</b></div><div><small>OPEN-SOURCE LICENSE EVIDENCE</small><b>{result.overview.license_files.join(", ") || "No license file detected — verify before contributing"}</b></div><div><small>SCANNED SIZE</small><b>{formatBytes(result.coverage.bytes_scanned)}</b></div></div><div className="guide-grid"><div><small>CONTRIBUTOR GUIDANCE</small><div className="guide-items">{result.overview.contributor_instructions.length ? result.overview.contributor_instructions.slice(0, 4).map(item => <span key={item}>{item}</span>) : <span>None identified in scanned files</span>}</div></div><div><small>RELEVANT DIRECTORIES</small><div className="directory-chips">{result.overview.directories.slice(0, 8).map(item => <span key={item}>{item === "." ? "/ root" : item}</span>)}</div></div><div><small>CI WORKFLOWS</small><div className="guide-items">{result.overview.ci_files.length ? result.overview.ci_files.slice(0, 3).map(item => <span key={item}>{item}</span>) : <span>No workflow file detected</span>}</div></div></div><p className="coverage-note"><span>↳</span> {result.coverage.warning}</p></div>
            <div className="tab-bar" role="tablist" aria-label="Analysis outputs"><button role="tab" aria-selected={activeTab === "evidence"} className={activeTab === "evidence" ? "active" : ""} onClick={() => setActiveTab("evidence")}>03 EVIDENCE <span>{result.evidence.length}</span></button><button role="tab" aria-selected={activeTab === "artifact"} className={activeTab === "artifact" ? "active" : ""} onClick={() => setActiveTab("artifact")}>04 ARTIFACT <span>↗</span></button><button role="tab" aria-selected={activeTab === "verification"} className={activeTab === "verification" ? "active" : ""} onClick={() => setActiveTab("verification")}>05 VERIFY <span>{result.checks.length}</span></button></div>
            {activeTab === "evidence" && <div className="tab-panel evidence-panel"><div className="panel-title"><span>LIKELY FILES TO READ FIRST</span><span className="subtle-count">RANKED / NOT FINAL</span></div>{result.candidates.length ? <div className="file-list">{result.candidates.slice(0, 7).map((file, i) => <div className="file-row" key={file.path}><span className={`file-emoji fe-${i % 4}`}>{file.path.endsWith(".md") ? "MD" : file.path.endsWith(".py") ? "PY" : "<>"}</span><div className="file-row-main"><b>{file.path}</b><small>{file.reason}</small></div><span className="file-score">{String(file.score).padStart(2, "0")}</span></div>)}</div> : <p className="empty-note">No high-confidence path candidates. Broaden the task description and inspect the full file list manually.</p>}<div className="evidence-title"><span className="evidence-title-mark">⌕</span><div><b>LINE-LEVEL RECEIPTS</b><small>Each citation points to text in the scanned snapshot.</small></div></div>{result.evidence.length ? <div className="evidence-list">{result.evidence.slice(0, 8).map((item, i) => <div className="evidence-card" key={`${item.path}:${item.line}:${i}`}><div className="evidence-card-meta"><span>{item.path}</span><b>L{item.line}</b></div><p>{item.text}</p>{item.context && <pre className="evidence-context">{item.context}</pre>}<div className="evidence-meter"><span style={{ width: `${Math.max(6, Math.min(100, item.relevance * 10))}%` }} /></div></div>)}</div> : <div className="empty-note">No line-level evidence was identified. Do not make a change based on ranking alone.</div>}</div>}
            {activeTab === "artifact" && <div className="tab-panel artifact-panel"><div className="artifact-head"><div><span className="banner-kicker">GENERATED / REVIEW FIRST</span><h4>Contribution brief</h4><p>Markdown · source-backed · no changes applied</p></div><div className="artifact-actions"><button onClick={() => download("PATCHTRACE_CONTRIBUTION_BRIEF.md", result.artifact)}>↓ Download</button><button onClick={() => void copyArtifact()}>{copied ? "✓ Copied" : "▣ Copy"}</button></div></div><pre className="artifact-preview">{result.artifact}</pre>{result.proposed_diff && <div className="diff-preview"><div className="panel-title"><span>PROPOSED DIFF / NOT APPLIED</span><button onClick={() => download("patchtrace-proposed-readme.patch", result.proposed_diff?.diff || "")}>↓ .patch</button></div><pre>{result.proposed_diff.diff}</pre><p>Review this change before applying it. The scanned repository snapshot has not been modified.</p></div>}</div>}
            {activeTab === "verification" && <div className="tab-panel verify-panel"><div className="panel-title"><span>EXECUTION LEDGER</span><span className="subtle-count">TRUTH OVER OPTICS</span></div><p className="verify-intro">A command listed in a README is not a test result. Run an offered check explicitly to capture its real exit status and output.</p>{result.checks.map(check => <div className="check-card" key={check.id}><div className="check-card-head"><div><b>{check.label}</b><code>{check.command}</code></div><Status value={check.status} /></div><p>{check.detail}</p>{check.output && <pre className={`check-output ${check.status}`}>{check.output}</pre>}{(check.id === "python_compile" || check.id === "pytest") && check.can_run !== false && <button className="check-run-button" disabled={Boolean(busyCheck)} onClick={() => void runCheck(check)}>{busyCheck === check.id ? "RUNNING…" : check.status === "not_run" ? `RUN ${check.id === "pytest" ? "TESTS" : "COMPILER CHECK"} ↗` : "RUN AGAIN ↻"}</button>}</div>)}</div>}
            </details>
          </> : <div className="results-empty"><div className="empty-asterisk">✳</div><div><h4>Nothing claimed. Nothing fabricated.</h4><p>Once you run an inspection, this area shows scanned files, real line references, a draft artifact and an honest check ledger.</p><button onClick={() => void inspect({ type: "demo", source: "", task: DEFAULT_TASK })} disabled={busy}>{busy ? "INSPECTING…" : "TRY THE SYNTHETIC DEMO ↗"}</button></div></div>}
        </div>
      </div>
    </section>

    <section id="proof" className="proof-section"><div className="proof-heading"><h2>Trust isn't a theme.<br /><span>It's an execution record.</span></h2></div><div className="proof-rules"><div className="proof-rule"><span>01</span><b>Every claim has an address.</b><p>File paths and line numbers come from the scan. Model-suggested paths are filtered against that manifest.</p></div><div className="proof-rule"><span>02</span><b>A proposal is not a patch.</b><p>Artifacts are downloadable suggestions. PatchTrace never applies changes automatically.</p></div><div className="proof-rule"><span>03</span><b>Not run means not run.</b><p>Check status distinguishes passed, failed and not run. A planned command is never recorded as a result.</p></div><div className="proof-rule"><span>04</span><b>AI isn't the source of truth.</b><p>Gemma proposes an investigation plan; deterministic code validates paths and records actual check results.</p></div></div></section>
    <footer className="footer"><div className="footer-logo">P<span>↗</span> patchtrace</div><p>Built to help humans make better contributions — not to pretend review is optional.</p></footer>
  </main>;
}

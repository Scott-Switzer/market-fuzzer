# Decision-Stability Workbook — Synthetic Benchmark Strategies

**Intermission document · M10 (market-fuzzer-native)**

> Purpose. This is a standing narrative record of what the current benchmark
> strategies do reliably, what "the same decision twice" means here, and where
> the picture is still incomplete. It is written for a technically literate
> reader who is not necessarily reading the code alongside it. It is *not* a
> changelog, a spec, or a migration note.

## 1. What "decision stability" means in this project

Market Fuzzer simulates multi-instrument trading worlds on a deterministic,
SQLite-backed exchange kernel. A *decision* is the strategy's chosen action at
a simulation step for a particular instrument: whether to submit, modify, or
cancel orders, and with what parameters. A *decision trace* is the sequence of
those actions across steps, instruments, and episodes.

In this setting, decision stability is not one property. It is a family of
related properties:

- **Determinism.** Given the same world definition and the same starting state,
  does the run produce byte-for-byte identical outputs — decisions, snapshots,
  events, and the files built from them?
- **Repeatability under reruns.** If you rebuild the same world and run it
  again, do you get the same decisions?
- **Structural stability across episodes.** If the strategy sees different
  random draws or different price paths but the same *kind* of market regime,
  do its decisions stay in a coherent band, or do they wander?
- **Snapshot consistency.** Does the recorded book snapshot match the book the
  observation was built from, and does the recorded decision match the decision
  the replay says was taken?

The project is built around making the first two properties *true by
construction* for the simulated kernel, and treating the last two as things you
can inspect but not fully guarantee yet.

## 2. What is currently stable by design

### 2.1 The exchange kernel is deterministic

The matching exchange used for benchmark and replay runs,
`app/exchange/v2.py`, is a SQLite-backed concurrency exchange built on top of
the Freewaves second-order market model. For the purposes of a benchmark run,
the kernel is deterministic: the same inputs, the same seed, and the same
sequence of submitted orders produce the same fills, the same ledger events,
and the same state transitions.

That is the foundation the rest of the stability story rests on. If the
underneath jitter, replay would be meaningless. The benchmark replay machinery
is explicit about this: it is not a "probably similar" comparison, it is a
validation that the simulated decisions produce the same state transitions as
the live run.

### 2.2 Benchmark runs are built from an explicit, versioned plan

A benchmark run is not an accidental collection of settings. It is assembled
from an explicit plan object, defined in `app/benchmark/plan.py`, and the
benchmark package exposes the plan-type error codes in `app/benchmark/__init__.py`
so that invalid or suspicious plan conditions are surfaced as named errors rather
than silent misconfigurations.

The practical consequence is that a repeatable run is, first, a *named and
reconstructible* run. If you know the plan and the seeds, you know the starting
conditions. That is what makes "same decisions twice" a question you can ask
rather than a question that has to remain rhetorical.

### 2.3 Snapshots are recorded against the exact book state used for the observation

The benchmark session (`app/benchmark/session.py`) captures the aggregate L10
depth of exactly the book state the observation was built from, *before* the
action touches it. It records:

- the snapshot id, episode id, decision index, global step, and instrument,
- the book depth used,
- the actual bid and ask levels,
- the ledger event watermark before and after the action.

This is important for auditability. If a reader wants to know "what did the
book look like when the strategy decided that?", the answer is in the
recording, not reconstructed from memory or from downstream aggregation.

The book snapshot schema is fixed in `app/corpus/schema.py`, and the book
snapshot rows are part of the seven canonical corpus tables. So snapshot
consistency is not an informal promise; it is a schema-bound row you can
validate.

### 2.4 Recording is memory-bounded and schema-bound

The corpus recorder (`app/corpus/recorder.py`) is designed so that recording a
run never silently becomes unbounded. The session wants to record decisions,
commands, events, and snapshots; the recorder keeps only bounded summary data
for its own bookkeeping — the last episode id and a max event watermark — and
the heavy write path is streamed into bounded Parquet writers rather than
accumulating per-episode lists in memory.

The recording contract is typed and protocol-checked: `SessionRecorder`,
`NullSessionRecorder`, and `CountingRecorder` all follow the same hook surface,
so a strategy session can be recorded or not without changing the decision logic
around it.

### 2.5 The corpus is hash-committed and re-validated on read

The corpus package (`app/corpus/__init__.py`, `app/corpus/validate.py`,
`app/corpus/builder.py`, `app/corpus/writer.py`) is structured so that a
produced corpus is not just "exported files." It is a manifest with a declared
schema version, declared table list in canonical order, and per-table logical
hashes. On read, the validator re-derives those hashes and compares them to the
manifest.

That means if anyone touches the data without going through the build path, the
corpus fails validation. That is a stability property too: the artifacts are
tamper-evident by design.

### 2.6 Replay is a first-class validation, not an afterthought

The benchmark replay path exists to answer "did this simulated world actually
do what we think it did?" rather than to provide a cute reproducibility
demo. The test suite for corpus integrity includes regression tests that
exercise exactly this kind of coupling — corrupted or out-of-sync rows should
fail validation with specific codes, not silently pass.

In other words, the project current state is: the simulation side is built so
that determinism and replay fidelity are testable and enforced, not merely
claimed.

## 3. What repeatability currently means in practice

### 3.1 Same seed + same plan + same starting state → same trace

For a truly identical rerun — same plan, same seed, same world definition, same
instrument set — the expectation is that the decisions, the snapshots, and the
resulting corpus rows are identical. That is the direct consequence of the
deterministic kernel, the explicit plan, and the schema-bound recordings.

This is the strongest stability property the project currently has, and it is
intended to be the baseline.

### 3.2 "The same decision twice" is a tightly scoped claim

It is worth being precise about what "the same decision twice" means:

- It means: same benchmark run specification, rerun from scratch.
- It does *not* currently mean: the strategy makes the same decision on
  different random noise but the same "regime."
- It does *not* currently mean: the strategy makes the same decision on a
  different universe, even a very similar one.

So repeatability today is a property of *runs*, and only indirectly a property
of the *strategy mind*. The strategy is exercised inside a world; the world is
the thing that is rerunnable.

### 3.3 Corruptions reach the right checks by construction

One practical sign that the stability story is attached to real machinery, not
sentiment, is that semantic corruptions actually reach the row-level checks.
The test helper that refreshes table hashes after a semantic corruption exists
so that, for example, a corrupted book snapshot row fails the row check and not
a file-level "file sha mismatch" that hides the real symptom.

That is a small thing, but it matters: it is the difference between "the corpus
looks intact" and "the corpus checks the right things."

### 3.4 Validation is specific, not generic

The validator in `app/corpus/validate.py` does not just check "rows exist." It
checks:

- manifest table order and completeness against the seven canonical tables,
- per-table logical hashes,
- numeric sanity on selected columns for episodes, book_snapshots, and other
  tables where "looks like an integer" matters,
- price and grain consistency on `securities` and `daily_bars`,
- episode presence for the supporting tables,
- and table-specific relationships such as the foreign-key-style checks between
  decisions/snapshots/events and episodes.

This makes the corpus a place where stability is inspected, not just a place
where data lands.

## 4. Known limits and where stability degrades or is unknown

### 4.1 Determinism holds for the simulated kernel; real-world strategy behavior may not

The project's stability story is strongest for the *simulated* benchmark world.
The exchange kernel is deterministic; the recording and replay path is built to
validate that determinism; the corpus is built to make the resulting artifacts
tamper-evident.

What this does *not* imply is that the strategies themselves are internally
stable in any deep sense. A strategy can be deterministic inside a rerun and
still behave very differently across regimes. That is a strategy-design question
and a market-model question, not a kernel-determinism question.

### 4.2 Repeatability is a run-level property, not a strategy-level property

Because decisions arise from a strategy inside a world, the most precise
repeatability claim is about runs. If you change the world — the instruments,
the regime, the seed, the depth, the agent instruments, the timing — you are no
longer making the same repeatability claim.

That is not a defect. It is a scope clarification. Readers should not infer
from this document that the project currently guarantees cross-world decision
stability.

### 4.3 The "strategy mind" is the least-known part

From a stability standpoint, the most uncertain layer is the strategy layer
itself: what rules drive the decisions, how sensitive they are to noise, and how
stable they are across regimes that the benchmark does not currently repeat.

Some of this uncertainty is inherent. The strategies are the thing being studied.
If they were already known to be stable across regimes, there would be less need
for the benchmark in the first place.

The honest statement is: the project has strong machinery for *observing* and
*validating* decisions; it has a weaker story for *explaining* why a strategy
made a particular decision or how fragile that decision is to changes in input.

### 4.4 Snapshots are recorded, but downstream stability reasoning is still manual

Snapshots are recorded at the right point in time and against the right book
state. That is good. What is weaker is any automated reasoning about what those
snapshots *imply* about strategy stability across episodes.

There is instrumentation — counts, watermarks, depth at decision time, ledger
watermarks before and after — but that is evidence, not an automated
stability verdict. A reader who wants to compare strategy behavior across episodes
still does the comparison, possibly using the corpus as the substrate.

### 4.5 The notebook/corpus boundary is intentional, but it leaves some questions unanswered

The corpus is built for training/evaluation datasets: versioned, hash-committed,
validated. The benchmark session is built for evaluation worlds: replayable,
deterministic, schema-bound.

What sits between them — the qualitative story of whether a strategy is stable in
any deeper sense — is currently more a matter of inspection than automation. The
corpus can tell you what happened; it does not yet, by itself, tell you whether
what happened is stable in a regime-general sense.

### 4.6 Known unknowns worth stating plainly

- Cross-regime decision stability is not asserted. The project does not
  currently claim that a strategy makes similar decisions across different price
  paths of the same broad type.
- Sensitivity of decisions to small input perturbations is not quantified here.
  That would require a perturbation study that is outside the current benchmark.
- Any statement about "the strategy is stable" should be read as "the run was
  stable," unless more is explicitly added later.
- If a reader wants a stronger stability claim, the work item is to define what
  "stability" would mean for the strategy in question and then build a study that
  measures exactly that.

## 5. What is known vs unknown, in actionable terms

| Question | Current answer | Basis |
|---|---|---|
| If I rerun the same benchmark world from the same plan and seed, will I get the same decisions? | Yes, in the intended design. | Deterministic exchange kernel, explicit plan, schema-bound recordings, replay validation. |
| Will the corpus files match? | Yes, if built through the normal build path. | Manifest + logical hashes + re-validation on read. |
| Will a semantic corruption slip through as a file mismatch instead of the real problem? | No, by design. | Hash-refreshing corruption helper; row-level checks. |
| Does the book snapshot match the book the observation used? | Yes, by construction, because the snapshot is taken before the action and recorded against the exact depth used. | `app/benchmark/session.py` recording path; snapshot schema in `app/corpus/schema.py`. |
| Does the recorded decision match the decision the replay says was taken? | Yes for simulated runs, because replay validates state transitions. | Benchmark replay machinery. |
| Will the strategy make the same decision on a similar but different world? | Not asserted. | Out of scope for current replay/determinism guarantees. |
| Will the strategy make the same decision under different noise in the same world? | Not asserted. | Not measured by current benchmark. |
| Is the recording memory-safe for long runs? | Yes, by construction. | Streaming into bounded writers; no per-episode decision/snapshot lists accumulated in the sink. |
| Is the corpus tamper-evident? | Yes. | Manifest + logical hashes + re-validation. |

## 6. How a reader should read this document

- Treat the stable-by-design items as *architecture-level claims*: they rest on
  the actual machinery in the files referenced later in this document.
- Treat the repeatability items as *run-level claims*: they apply when the run
  specification is kept identical.
- Treat the limits as *scope clarifications*, not as apologies. The project is
  explicit about what it does and does not guarantee.
- Treat the known-unknowns as a short menu of possible next studies, not as a
  failure of the current work.

## 7. Supporting implementation references

These are cited as evidence for the claims above, not as the primary artifact.
The primary artifact is this document.

- Deterministic exchange and replay setting: `app/exchange/v2.py`
- Benchmark plan, types, and exported plan error codes: `app/benchmark/plan.py`,
  `app/benchmark/__init__.py`
- Session recording path and snapshot capture: `app/benchmark/session.py`
- Recording contract and bounded recorders: `app/corpus/recorder.py`
- Corpus schemas: `app/corpus/schema.py`
- Corpus manifest, build, and validation: `app/corpus/__init__.py`,
  `app/corpus/builder.py`, `app/corpus/validate.py`, `app/corpus/writer.py`
- Corpus integrity regression tests: `tests/test_corpus_release.py`,
  `tests/test_corpus_split_safety.py`

## Self-review

### What this artifact is

This is a narrative, auditable intermission document describing the current
decision-stability properties of the synthetic benchmark strategies in
market-fuzzer-native. It is written as prose for a technically literate reader.

### What this artifact is not

It is not code, not a test, not a spec, not a migration plan, and not a claim
that the strategies are stable in any deep regime-general sense. It does not
modify runtime behavior.

### What was validated

- The document's claims were read against the current implementation files for
  the deterministic kernel, the benchmark plan/session, the recorder, the corpus
  schema/validate/builder/writer, and the corpus regression tests.
- The stability claims in the document are intentionally scoped to match what the
  implementation actually supports: run-level repeatability, snapshot-at-decision
  consistency, hash-committed tamper-evident corpus, and replay-validated
  simulated runs.
- The known-limits section was written to avoid overclaiming; it explicitly does
  not assert cross-world or regime-general stability.

### What was not validated

- A real perturbation or regime-comparison study was not performed. Any statement
  about deeper strategy stability would require such a study.
- This document does not verify itself by running a strategy. It is a
  documentation artifact, and its supporting evidence is the implementation and
  test files cited above.

### Exact reproduction steps a reader can use

1. Check out the same HEAD and read the cited files in this document:
   `app/exchange/v2.py`, `app/benchmark/plan.py`, `app/benchmark/__init__.py`,
   `app/benchmark/session.py`, `app/corpus/recorder.py`,
   `app/corpus/schema.py`, `app/corpus/__init__.py`,
   `app/corpus/builder.py`, `app/corpus/validate.py`,
   `app/corpus/writer.py`, `tests/test_corpus_release.py`,
   `tests/test_corpus_split_safety.py`.
2. For each claim in Section 2, find the corresponding implementation point in
   those files and confirm that the claim is supported rather than rhetorical.
3. For each "not asserted" claim in Section 4 and the known/unknown table in
   Section 5, confirm that the document does not secretly rely on an
   implementation guarantee that does not exist.
4. Confirm that any in-repo links or file references in this document point to
   files that actually exist at the stated paths.

### Remaining assumptions and gaps

- The document assumes the implementation files cited are the current source of
  truth for the claims made. If the implementation changes, the document should
  be revisited.
- The document assumes "stability" should be read primarily as run-level
  repeatability and snapshot/record consistency, not as a generic strategy
  maturity claim.
- The biggest gap is the absence of an automated regime-comparison or
  perturbation-based stability study. If that is wanted, it is a separate task
  and should be defined before it is claimed.

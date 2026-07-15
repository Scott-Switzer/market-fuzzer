# Decisions

## Market Fuzzer rebuild decisions

- The existing deterministic exchange and validation infrastructure remains intact; the new product layer is an adapter, not a new backend service.
- Built-in strategies only are supported in the browser. Arbitrary code execution is intentionally excluded.
- The no-key deterministic hypothesis and test path is the default. AI suggestions cannot influence fills or verdicts.
- The fragile POV tutorial is deliberately defective and labelled as such, so the end-to-end testing experience demonstrates a software defect rather than a live-market claim.

1. **Internal exchange is the default backend.** ABIDES integration was bounded and excluded from the judge-critical path because both public ABIDES repositories are stale or archived. The original engine keeps installation fast and provenance clear.
2. **Integer ticks and cents are mandatory.** Matching and accounting avoid floating-point equality errors.
3. **Common seeds isolate scenarios.** Scenario mutations change controlled fields while preserving the experiment and seed set.
4. **Historical Market Fuzzer decision: world generation was primary; execution testing was the proof.** That earlier synthetic-world console remains preserved as research infrastructure. The current primary product is Quant Challenge Arena, documented in the Arena integration ADR and Build Week work log.
5. **Offline mode is complete.** GPT-5.6 compiles structured worlds and assumptions when a key is present; it never determines prices or fills. Judges can complete the workflow without a key.
6. **No aggregate realism score.** Every diagnostic reports its component, target, status, and limitation.
7. **Threshold prose is evidence-gated.** Failure language appears only after repeated cells exceed a declared cost-change rule.
8. **Vanilla browser client over React.** The current interface is dependency-light and judge-friendly while preserving the four-view product workflow.

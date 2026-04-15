# Hermes Meta-Router E2E Hardening Plan

> For Hermes: follow this checklist exactly, update statuses as work progresses, and do not claim a fix is complete until the matching validation has been run.

Goal: make Hermes the real end-to-end meta-router runtime: ingress classification parity, SoM/EOP/MR-ALS execution, outcome logging, and final user-visible delivery gating/enrichment.

Architecture summary:
- Keep `gateway/meta_router_runtime.py` as the shared route-decision authority.
- Make all Hermes ingress surfaces route through the shared runtime with one bypass contract.
- Make post-turn SoM/EOP evaluation produce a structured receipt/result that Hermes uses to decide what the user actually sees.
- Add regression tests for routing, bypass, gateway parity, and delivery behavior before broad refactors.

Tech stack:
- Python runtime in `/home/samade10/.hermes/hermes-agent`
- Shared MR-ALS artifacts in `/home/samade10/.openclaw/workspace/skills/maintainer/meta-router/experience/`
- SoM pipeline scripts in `/home/samade10/.openclaw/workspace/rql/scripts/`

---

## Verified current context (from live VM)

1. Hermes CLI currently classifies and can run SoM phase 1 / phase 2, but final replies still do not reliably include Oracle/REF/scorecard.
2. Failed SoM runs can still return normal-looking success answers.
3. Telegram currently prepends directives directly in `gateway/platforms/telegram.py` instead of using the shared runtime path.
4. `run_agent.py` still hardcodes `source="cli", surface="cli"` for runtime routing and only skips routing on a Hermes-specific len>=10 heuristic.
5. `routing_events.jsonl` is active, but `routing_outcomes.jsonl` coverage for real Hermes sessions is sparse.
6. There are currently no focused tests for `meta_router`, `meta_router_runtime`, `meta_router_executor`, or `meta_router_server`.

Primary evidence reviewed before planning:
- `/home/samade10/.hermes/hermes-agent/run_agent.py`
- `/home/samade10/.hermes/hermes-agent/gateway/meta_router.py`
- `/home/samade10/.hermes/hermes-agent/gateway/meta_router_runtime.py`
- `/home/samade10/.hermes/hermes-agent/gateway/meta_router_executor.py`
- `/home/samade10/.hermes/hermes-agent/gateway/meta_router_server.py`
- `/home/samade10/.hermes/hermes-agent/gateway/platforms/telegram.py`
- `/home/samade10/.openclaw/workspace/skills/maintainer/meta-router/STATUS.md`
- `/home/samade10/.openclaw/workspace/skills/skill-first-workflow/SKILL.md`
- `/home/samade10/Obsidian-Vault/reports/2026-04-07-som-v3-implementation-report.md`
- `/home/samade10/Obsidian-Vault/reports/2026-04-07-meta-router-als-reality-based-prd.md`
- `/home/samade10/Obsidian-Vault/reports/2026-04-10-meta-router-somv3-als-adversarial-audit.md`

---

## Acceptance criteria

A fix is only complete when all of these are true:

- All Hermes ingress surfaces under scope use one shared bypass + routing decision path.
- Hermes CLI and Telegram/gateway attribute routing events with correct `source`/`surface` values.
- A routed turn that fails SoM/ADV_PASS is not delivered as an unqualified success answer.
- A routed turn that completes evaluation includes an explicit receipt block in the user-visible answer with at least:
  - Meta-router directive
  - routing artifact version
  - Oracle verdict
  - RQL/SoM score + verdict
  - ADV_PASS result when run
  - state dir path
- `routing_outcomes.jsonl` gets correlated rows for real Hermes sessions, not just synthetic test IDs.
- Focused automated tests exist for routing/bypass/server/delivery logic.
- Live E2E validation with `hermes chat -Q -q ...` proves the final visible answer matches backend artifacts.

---

## Execution checklist

Status legend:
- [ ] not started
- [-] in progress
- [x] completed
- [!] blocked / needs redesign

### Phase 0 — Safety + baseline
- [x] Record current baseline evidence (git status, active artifact, current event/outcome counts).
- [ ] Remove or quarantine incidental repo noise from this task (`gateway/meta_router.py.bak` etc.) so verification stays clean.

### Phase 1 — Tests first (RED)
- [x] Add focused unit tests for shared bypass + route decision behavior.
  - Files:
    - create `tests/gateway/test_meta_router_runtime.py`
  - Cover:
    - bypass contract for short/yes-no/command/numeric inputs
    - `make_route_decision()` source/surface attribution
    - active artifact version propagation
- [x] Add focused API/server tests.
  - Files:
    - create `tests/gateway/test_meta_router_server.py`
  - Cover:
    - `/classify` response schema
    - source/surface passthrough in request body if added
    - bypass handling consistency
- [x] Add focused post-turn delivery tests.
  - Files:
    - create `tests/gateway/test_meta_router_delivery.py`
  - Cover:
    - successful routed turn adds receipt block to final response
    - failed SoM/ADV_PASS does not return unqualified success
    - outcome rows written for real request IDs
- [x] Add Telegram/gateway ingress parity test.
  - Files:
    - create `tests/gateway/test_telegram_meta_router.py`
  - Cover:
    - Telegram batching path no longer direct-prepends via legacy `classify()`
    - gateway messages use shared runtime semantics

### Phase 2 — Shared routing contract
- [ ] Introduce one shared bypass predicate/helper in the routing layer.
  - Likely files:
    - modify `gateway/meta_router_runtime.py`
    - possibly modify `gateway/meta_router.py`
- [ ] Update `run_agent.py` to use shared source/surface derivation instead of hardcoded `cli/cli`.
  - For CLI: `source=cli`, `surface=cli`
  - For gateway surfaces: `source=gateway`, `surface=<platform>`
- [ ] Update `gateway/platforms/telegram.py` so Telegram no longer uses direct `classify()/prepend_directive()` as a separate path.
- [ ] Update `gateway/meta_router_server.py` request/response contract if needed so HTTP/plugin callers can pass explicit source/surface/session metadata.

### Phase 3 — Delivery gating and receipt enrichment
- [ ] Refactor `gateway/meta_router_executor.py` to return a structured phase-2 result for synchronous callers.
  - Include:
    - score / verdict
    - oracle verdict
    - adv_pass_clean + finding count
    - delivery gate status
    - score card / ref entry / delivery path if available
    - fix prompt path if failed
- [ ] Update `run_agent.py` to use the structured phase-2 result before returning the final answer.
- [ ] Add a standard receipt block formatter for successful routed turns.
- [ ] Add a failure-mode formatter for failed routed turns.
  - Must clearly say the backend evaluation failed.
  - Must not present the raw answer as final success.

### Phase 4 — Retry/fix-loop enforcement
- [ ] Decide the minimal safe behavior for failed SoM turns:
  - option A: block and return evaluated failure receipt + fix prompt summary
  - option B: one bounded automatic repair pass using `fix_prompt.md`, then re-evaluate
- [ ] Implement the chosen behavior in `run_agent.py` / `gateway/meta_router_executor.py`.
- [ ] Ensure this does not create unbounded recursion or hidden second-turn loops.

### Phase 5 — Outcome logging completeness
- [ ] Identify early-return paths inside `run_agent.py` that currently bypass MR outcome logging.
- [ ] Add a single finalization path or equivalent guard so routed turns log outcomes even on retry exhaustion / interruption / parser failure.
- [ ] Ensure `run_outcome_only()` also participates in threshold counting if that is still the intended MR-ALS behavior.
- [ ] Reconcile `composite_score` semantics with EOP/ADV_PASS when adversarial evaluation runs.

### Phase 6 — Verification
- [ ] Run focused test files.
- [ ] Run syntax/compile checks on touched Python files.
- [ ] Run live CLI E2E probes with at least:
  - successful code task
  - failing/insufficient code task
  - audit/review task
  - production task
- [ ] Confirm user-visible final responses match backend artifacts for those probes.
- [ ] Confirm event/outcome request IDs correlate for real Hermes sessions.

### Phase 7 — Clean finish
- [ ] Update this plan/checklist to reflect actual completion state and any scope changes.
- [ ] Commit Hermes repo changes with a focused message.
- [ ] If shared workspace files are changed, commit those separately.
- [ ] Summarize what is fixed vs still intentionally deferred.

---

## Concrete implementation order

1. Write failing tests for bypass/runtime/server/delivery.
2. Unify routing contract + Telegram/gateway ingress.
3. Build structured phase-2 result + receipt formatter.
4. Enforce failed-evaluation behavior before final delivery.
5. Improve outcome-finalization coverage.
6. Re-run tests and live E2E checks.
7. Update this checklist and commit.

---

## Risks / tradeoffs

- Making post-turn evaluation synchronous for all gateway surfaces may increase latency. If so, preserve synchronous behavior for CLI first and explicitly document gateway tradeoffs.
- Automatic repair loops can create runaway behavior if not bounded to one retry and one re-evaluation.
- Changing Telegram ingress behavior may affect batching assumptions; keep tests tight around that adapter.
- Shared OpenClaw plugin parity is not the primary target until Hermes-side behavior is correct, but API request metadata changes should avoid making that harder.

---

## Validation commands to use during execution

- Syntax:
  - `python3 -m py_compile gateway/meta_router.py gateway/meta_router_runtime.py gateway/meta_router_executor.py gateway/meta_router_server.py run_agent.py`
- Tests:
  - `python3 -m pytest tests/gateway/test_meta_router_runtime.py -q`
  - `python3 -m pytest tests/gateway/test_meta_router_server.py -q`
  - `python3 -m pytest tests/gateway/test_meta_router_delivery.py -q`
  - `python3 -m pytest tests/gateway/test_telegram_meta_router.py -q`
- Live E2E:
  - `hermes chat -Q --source mr-e2e -q 'Create a tiny Python add(a, b) function with a docstring and return statement.'`
  - `hermes chat -Q --source mr-e2e -q 'Create a tiny Python multiply(a, b) function.'`
- Artifact checks:
  - inspect `routing_events.jsonl`, `routing_outcomes.jsonl`, matching `rql/state/*`, and matching `session_*.json`

---

## Current execution status

- Research complete: yes
- Plan written: yes
- Implementation started: yes
- Hermes worktree restored to the intended `sam/custom-hermes` branch after verifying `main` no longer contained the meta-router runtime modules the live service expected.
- Focused Hermes meta-router tests: passing (`28 passed` across gateway + doctor targets on 2026-04-15).
- Adaptive-learning hygiene follow-up: completed in workspace scripts with dedicated tests (`6 passed`).
- Latest live E2E probe: `hermes chat -Q --source mr-e2e -q 'Create a tiny Python multiply(a, b) function with a docstring and return statement.'`
  - visible receipt persisted to session `20260415_165047_08a3e2`
  - routed state dir: `/home/samade10/.openclaw/workspace/rql/state/create-a-tiny-python-multiply--203d`
  - delivery remained blocked because score `64 < trivial threshold 65`
- Latest MR-ALS status after regeneration:
  - `routing_events.jsonl`: 162 rows
  - `routing_outcomes.jsonl`: 12 rows
  - eligible outcome-enriched production rows: 0
  - active candidate: `candidate-0002`
  - rollout mode: `shadow`
  - evidence maturity: `shadow-only`
  - promotion ready: `false`

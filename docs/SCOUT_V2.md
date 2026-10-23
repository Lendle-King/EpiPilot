# Bounded Research Scout (v2)

EpiPilot asks the **existing** Codex/Kimi coding agent to investigate unknown
parts of a research space. It does not run a second LLM agent.

## Budgeted workflow

1. **epi onboard** inventories research documents without treating them as facts.
2. **epi scout prepare** returns a read-only frontier: goal, Unknowns, Claims,
   qualified Evidence, pinned Git revision and budget.
3. **epi scout start --operation SCOUT-1 --reserve 0.5** reserves explicit
   research cost units and prevents parallel Scout operations.
4. The coding agent inspects **committed** sources, alternative explanations,
   and discriminative experiments, then writes a bounded JSON submission.
5. **epi scout submit --file scout-submission.json** checks committed blob
   hashes and exact source lines, records actual cost and stages all findings.
6. A human explicitly reviews every provisional lead:

~~~bash
epi scout review --candidate ranking-failure --decision accepted \
  --reason "Falsifiable with the current benchmark"
~~~

Accepted leads create an **Unknown, provisional Claim, and Proposal**, not
scientific Evidence. Rejection leaves the scientific frontier intact.
7. Use **epi iterate**, then ordinary preregister/start/record/verify/reflect
   commands. A scientific conclusion still needs an independent validated run.

A Scout on the same Git revision and unchanged research frontier is rejected;
adding no-op or failed Scout events cannot mint a new research epoch.

## Source-grounded submission shape

Replace the source quote, line and SHA-256 with an **actual committed document**.
This is only a JSON structure example, not a real experimental observation:

~~~json
{
  "operation_id": "SCOUT-1",
  "actual_cost": 0.3,
  "status": "completed",
  "summary": "Examined matched historical experiments and alternative mechanisms.",
  "candidates": [
    {
      "id": "ranking-failure",
      "operation_id": "SCOUT-1",
      "question": "Does ranking explain the repeated failures?",
      "hypothesis": "Ranking errors are responsible for repeated failures.",
      "scope": "matched held-out evaluation set",
      "title": "Matched ranking ablation",
      "rationale": "Hold controller and seeds fixed while intervening on ranking.",
      "acceptance": "Frozen matched-seed metric comparison",
      "kind": "explore",
      "source": {
        "path": "docs/previous-experiment.md",
        "line": 12,
        "exact_quote": "Observed repeated ranking errors under matched seeds.",
        "sha256": "REPLACE_WITH_REAL_64_CHARACTER_LOWERCASE_HEX_DIGEST"
      },
      "information_gain": 0.8,
      "expected_improvement": 0.2,
      "estimated_cost": 0.6,
      "risk": 0.1
    }
  ]
}
~~~

For a failed Scout use status "failed" with empty candidates, real incurred
cost, and a meaningful summary. An empty successful result is also valid.

## Evidence and security boundaries

- No automatic public web research, extra model process, GitHub/Notion mutation
  or unattended execution. The coding agent uses its existing permissions.
- A Git citation establishes **wording**, not scientific truth. A provisional
  Scout hypothesis cannot set measurement_validated or a supported Claim.
- Source files are bounded UTF-8 regular committed Git blobs. Symlinks,
  secrets-like paths, oversized files, dirty tracked worktrees and fabricated
  source lines are rejected before appending an event.
- Costs are reserved before work, actual costs are recorded even when they
  exceed the reservation, and budget overruns block further research.
- The host must not submit private credentials or traces in Scout artifacts.
- This is a compatibility adapter, **not** wholesale merging of the old
  research-scout-v1 SQLite runtime or its domain-specific runners.

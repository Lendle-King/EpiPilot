# ChatGPT Web handoff via agent-browser (experimental)

This implementation delegates **all GitHub and Notion work to ChatGPT**. EpiPilot does not connect to the GitHub API or Notion API in this workflow. It only generates a task packet, opens a visible ChatGPT browser window, and records a manually supplied **unverified** receipt.

## Requirements

- Python 3.11+, EpiPilot installed in editable mode
- [agent-browser](https://github.com/vercel-labs/agent-browser), Apache-2.0
- A ChatGPT account with the appropriate Work/Codex features and authorized GitHub/Notion access. Actual capabilities vary by account and mode.

Install agent-browser:

    npm install -g agent-browser
    agent-browser install

## Usage

Prepare a local context file, such as /tmp/epipilot-context.txt, describing the task constraints, acceptance criteria, budget, and expected evidence. Do not include credentials.

    python -m epipilot.executors.chatgpt_handoff prepare \
      --task-id experiment-001 \
      --objective "Verify an evidence-backed improvement" \
      --context-file /tmp/epipilot-context.txt \
      --github https://github.com/Lendle-King/EpiPilot \
      --notion https://www.notion.so/REPLACE_WITH_PAGE_ID

The command prints the location of a private prompt.md. Open the browser with:

    python -m epipilot.executors.chatgpt_handoff open --task-id experiment-001

**Manually** paste prompt.md into ChatGPT Work, authorize the appropriate GitHub/Notion permissions, and confirm any consequential actions. Ask ChatGPT to report experiment configs, Git SHA, PR links, workflow-run links, outcomes, and Notion updates. Do not assume standard GitHub read-only access permits writing code or running experiments.

After verifying what ChatGPT actually reported, create /tmp/epipilot-receipt.json:

    {
      "summary": "ChatGPT reports that the experiment ran; independent verification is pending.",
      "github_evidence": ["https://github.com/ORG/REPO/actions/runs/ID"],
      "notion_evidence": ["https://www.notion.so/ACTUAL_PAGE_ID"],
      "limitations": ["Results have not yet been independently verified."]
    }

Record the report without promoting it to a canonical fact:

    python -m epipilot.executors.chatgpt_handoff report \
      --task-id experiment-001 --receipt-file /tmp/epipilot-receipt.json
    python -m epipilot.executors.chatgpt_handoff status --task-id experiment-001
    python -m epipilot.executors.chatgpt_handoff close --task-id experiment-001

## State, security, and recovery

- The local handoff state is **not** EpiPilot's canonical Event Store, and cannot transition a Task to PASSED.
- A GitHub/Notion link in the manual receipt is an unverified assertion. Check actual logs, parameters, artifacts and page changes through an independent verification process before promoting evidence.
- The adapter deliberately does not submit prompts, scrape ChatGPT answers, inspect DOM, bypass confirmation or automate repeated ChatGPT requests. OpenAI terms restrict automated/programmatic extraction from consumer services.
- The default state directory is ~/.local/share/epipilot/chatgpt-handoffs, outside the repository. Files are private on POSIX. Never commit real handoffs, credentials or browser state.
- Each task ID yields a stable hashed browser session, enabling manual re-opening after a crash. Prompt preparation is idempotent, but a changed request requires a new task ID. Browser authentication remains under agent-browser's restore implementation.
- Persistent restore state can include cookies. Set AGENT_BROWSER_ENCRYPTION_KEY (64-character hex) for encryption at rest and protect it separately. Do not enter credentials into prompts.
- Current agent-browser disallows --allowed-domains together with --restore. If strong origin isolation is required, use operating-system/network controls.
- This milestone is **supervised handoff only**, not a headless executor integrated with TaskRuntime. Work/Codex execution and EpiPilot canonical verification still require separate integration.

## Next step

When authorization and an independently verifiable results channel are available, add a runtime event for delegation submission, a reconciliation service to recover interrupted handoffs, and an independent verifier. Do not implement automatic PASSED on ChatGPT self-reports.

## References

- https://github.com/vercel-labs/agent-browser
- https://agent-browser.dev/security
- https://help.openai.com/en/articles/20001275/
- https://openai.com/policies/terms-of-use/

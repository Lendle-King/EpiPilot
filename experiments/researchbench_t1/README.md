# ResearchBench-T1 (experimental, offline by default)

See [ResearchBench-T1 protocol](../../docs/RESEARCHBENCH_T1.md) for the fixed
research goals, stages, costs, heldout protocol, dataset acquisition and
Codex/Kimi controlled-policy comparison requirements.

A no-network, no-agent, zero-paid-API smoke test using real data:

```bash
python -m pip install -e '.[t1]'
python experiments/researchbench_t1/t1.py smoke \
  --work /tmp/t1-smoke-work --vault /tmp/t1-smoke-vault
```

Use `python experiments/researchbench_t1/t1.py --help` for all commands.
`pilot` and `full` require explicit `--allow-agent-stage` when initialized.

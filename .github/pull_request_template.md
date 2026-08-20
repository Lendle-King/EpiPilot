## Summary

Describe the problem and the smallest coherent change that solves it.

## Why this change

What requirement, bug, evidence, or architecture decision motivates this change?

## Invariants / contracts affected

Check all that apply and explain below when relevant.

- [ ] Task completion / independent verification
- [ ] Observation -> evidence -> fact boundary
- [ ] Task or hypothesis state transitions
- [ ] Plan mutation provenance
- [ ] Canonical state / context separation
- [ ] Event replay / persistence
- [ ] Executor isolation or authority
- [ ] Resource locking / concurrency
- [ ] Security / privacy boundary
- [ ] None of the above

## Testing

List the tests added or changed and the commands run.

```text
ruff format --check .
ruff check .
mypy src
pytest
```

## Compatibility / migration

Does this change alter a public API, persisted schema, event payload, configuration format, or replay semantics? If yes, describe the migration or compatibility strategy.

## Security and privacy

Confirm that no credentials, private transcripts, private repository content, production logs, or unsanitized external responses are included.

- [ ] I reviewed this change for accidental sensitive data.

## Review notes

Call out non-obvious trade-offs, deferred work, or areas where reviewers should be especially strict.

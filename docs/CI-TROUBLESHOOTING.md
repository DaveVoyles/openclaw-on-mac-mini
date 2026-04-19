# CI Troubleshooting Guide

<!-- Updated: 2026-04-18 -->

Quick reference for diagnosing and fixing CI failures on this repo.

## Common Failures

### Lint / Format failures

**Symptom**: `ruff check` or `ruff format --check` fails
**Fix**:
```bash
make lint-fix   # auto-fix most issues
make format     # fix formatting only
```
Then commit the changes and push.

### Smoke test failures

**Symptom**: `pytest -m smoke` fails
**Fix**:
```bash
make smoke-verbose   # run locally with verbose output
python3 -m pytest tests/test_config.py -v   # run specific smoke file
```
Smoke failures are usually import errors or config issues — check for missing env vars with `make validate-env`.

### Full suite failures

**Symptom**: Full pytest run fails but smoke passes
**Fix**:
```bash
# Run the failing test locally
python3 -m pytest tests/<failing_file>.py -v -k "<test_name>"

# Check for flaky tests (run 3x)
python3 -m pytest tests/<file>.py -v --count=3
```

### Type check failures (mypy)

**Symptom**: `scripts/mypy_enforce.py` fails
**Fix**: Add or correct type annotations on functions in the failing files.
See `python3 scripts/mypy_enforce.py` for specific errors.

### Dependency audit failures (pip-audit)

**Symptom**: `pip-audit` reports CVEs
**Fix**: Update the affected package in `requirements.txt` — change the `~=X.Y` to `~=X.Z` where Z is the patched version. Run `make smoke` to verify compatibility.

### Cache issues

**Symptom**: CI fails in unexpected ways after a passing run
**Fix**: Re-run the CI job from the GitHub Actions UI (bypasses the cache). If it passes on re-run, the issue was a stale cache entry.

## Checking CI Status Locally

```bash
make ci   # runs lint + smoke + typecheck locally
```

## Useful Debug Commands

```bash
make validate-env           # check for missing env vars
make smoke-verbose          # smoke tests with full output
python3 -m pytest --tb=short tests/<file>.py   # concise tracebacks
python3 -m pytest --co -q   # collect only (no execution)
```

## When to Ask for Help

If CI is failing and:

- `make ci` passes locally but CI fails → likely environment difference, check `.env` and system deps
- Same test fails consistently → genuine regression, investigate the change
- Random test fails intermittently → flaky test, investigate isolation

## Related Docs

- [TESTING.md](TESTING.md) — full testing strategy and test markers
- [DEVELOPMENT.md](DEVELOPMENT.md) — local dev setup
- [OPERATIONS-RUNBOOK.md](OPERATIONS-RUNBOOK.md) — production operations

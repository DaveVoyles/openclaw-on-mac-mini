#!/usr/bin/env python3
"""Track mypy enforcement progress over time."""
import subprocess, json, datetime

def collect_metrics():
    """Collect current mypy metrics."""
    result = subprocess.run(
        ["mypy", "src/", "--show-error-codes", "--ignore-missing-imports"],
        capture_output=True, text=True
    )
    
    lines = result.stdout.split('\n')
    errors = len([l for l in lines if ': error:' in l])
    warnings = len([l for l in lines if ': warning:' in l])
    
    return {
        "timestamp": datetime.datetime.now().isoformat(),
        "errors": errors,
        "warnings": warnings,
        "total_issues": errors + warnings
    }

# Save metrics
metrics = collect_metrics()
print(json.dumps(metrics, indent=2))

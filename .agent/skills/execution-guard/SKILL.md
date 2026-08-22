---
name: execution-guard
description: Stops a script or subprocess hanging forever on an external API. Use when writing any automated runner that calls Slack, Drive, or a note-taker.
---

# Execution Guard Skill

A utility skill to prevent scripts and subprocesses from running indefinitely (hanging). This is critical for automated runners like `daily_update_runner.py` that depend on external APIs (Slack, Fathom, Google Drive).

## Usage

### 1. Functional Timeout (Python Decorator)
Use this to wrap any Python function that might hang (e.g., due to a network request).

```python
from execution_guard import timeout_guard

@timeout_guard(seconds=60)
def fetch_data():
    # If this takes > 60s, a TimeoutException is raised
    pass
```

### 2. Block Timeout (Context Manager)
Use this for arbitrary blocks of code.

```python
from execution_guard import ScriptGuardian, TimeoutException

try:
    with ScriptGuardian(seconds=120):
        # Long running block
        pass
except TimeoutException:
    print("Code block took too long!")
```

### 3. Safe Subprocess Execution
Use this instead of raw `subprocess.Popen` or `subprocess.run` to ensure entire process groups are cleaned up on timeout.

```python
from execution_guard import safe_run_subprocess

result = safe_run_subprocess(["python3", "heavy_script.py"], timeout=300)
if not result["success"]:
    print(f"Failed: {result.get('message', 'Unknown error')}")
```

## Implementation Details
- **Linux**: Uses `signal.SIGALRM` for Python code blocks and `os.killpg` for subprocesses.
- **Windows**: Fallback to standard `subprocess` timeout and no-op for Python blocks (Windows lacks SIGALRM).

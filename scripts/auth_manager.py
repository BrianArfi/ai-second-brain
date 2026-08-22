#!/usr/bin/env python3
"""
Unified Google Services Auth Manager
Handles periodic token refresh for Calendar and Drive across multiple profiles.
"""
import os
import sys
import subprocess
import datetime

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Service Map
SERVICES = [
    {
        "name": "Default Calendar",
        "script": os.path.join(REPO_DIR, ".agent/skills/google-calendar-connector/gcal_manager.py"),
        "args": ["sweep", "--profile", "default", "--output", "text"],
        "timeout": 180
    },
    {
        "name": "Work Calendar",
        "script": os.path.join(REPO_DIR, ".agent/skills/google-calendar-connector/gcal_manager.py"),
        "args": ["sweep", "--profile", "work", "--output", "text"],
        "timeout": 180
    },
    {
        "name": "Secondary Calendar",
        "script": os.path.join(REPO_DIR, ".agent/skills/google-calendar-connector/gcal_manager.py"),
        "args": ["sweep", "--profile", "secondary", "--output", "text"],
        "timeout": 180
    },
    {
        "name": "Default Drive",
        "script": os.path.join(REPO_DIR, ".agent/skills/personal-drive-connector/gdrive_manager.py"),
        "args": ["search", "--query", "dummy_check_auth"],
        "timeout": 180
    },
    {
        "name": "Work Drive",
        "script": os.path.join(REPO_DIR, ".agent/skills/work-drive-connector/gdrive_manager.py"),
        "args": ["search", "--query", "dummy_check_auth"],
        "timeout": 180
    },
    {
        "name": "Secondary Drive",
        "script": os.path.join(REPO_DIR, ".agent/skills/secondary-drive-connector/gdrive_manager.py"),
        "args": ["search", "--query", "dummy_check_auth"],
        "timeout": 180
    }
]

def log(message):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_message = f"[{timestamp}] {message}"
    print(full_message)

def run_service_check(service):
    name = service["name"]
    script = service["script"]
    args = service["args"]
    timeout = service["timeout"]

    if not os.path.exists(script):
        log(f"[ERROR] {name}: Script not found at {script}")
        return False

    log(f"Refreshing {name}...")
    try:
        # Run with timeout to prevent hanging if manual auth is needed
        cmd = [sys.executable, script] + args
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        
        if result.returncode == 0:
            log(f"[SUCCESS] {name} refreshed.")
            return True
        else:
            # Check if output contains "Authentication Required" or similar
            if "Authentication Required" in result.stdout or "Authentication Required" in result.stderr:
                log(f"[MANUAL] {name} requires manual re-authentication.")
            else:
                log(f"[FAILED] {name} (Exit Code: {result.returncode})")
                if result.stderr:
                    log(f"  Error details: {result.stderr.strip().splitlines()[0]}")
            return False
            
    except subprocess.TimeoutExpired:
        log(f"[TIMEOUT] {name} timed out after {timeout}s (Waiting for input?)")
        return False
    except Exception as e:
        log(f"[ERROR] {name}: {e}")
        return False

def main():
    log("=== Google Services Token Refresh Routine Started ===")

    success_count = 0
    total_count = len(SERVICES)
    failed_names = []

    for service in SERVICES:
        if run_service_check(service):
            success_count += 1
        else:
            failed_names.append(service["name"])

    log(f"=== Routine Finished: {success_count}/{total_count} services healthy ===")
    if failed_names:
        log(f"Failed services: {', '.join(failed_names)}")
    log("-" * 40)

if __name__ == "__main__":
    main()

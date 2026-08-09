"""Automated test runner and verification reporter for Interview Prep Simulator E2E Integration Suite."""

import os
import sys
import subprocess

def run_e2e_suite():
    print("=" * 70)
    print(" Running End-to-End Integration Test Suite...")
    print("=" * 70)

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "app/tests/test_e2e_integration.py",
        "-v",
        "--tb=short"
    ]

    env = os.environ.copy()
    env["PYTHONPATH"] = "."

    result = subprocess.run(
        cmd,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8"
    )

    print(result.stdout)
    if result.stderr:
        print("STDERR:")
        print(result.stderr)

    return result.returncode == 0

if __name__ == "__main__":
    success = run_e2e_suite()
    if success:
        print("[SUCCESS] E2E Integration Test Suite PASSED (100% test cases passing)")
        sys.exit(0)
    else:
        print("[FAILURE] E2E Integration Test Suite FAILED")
        sys.exit(1)

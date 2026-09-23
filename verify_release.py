"""Offline full-release test runner. Never calls an AI provider."""
import subprocess
import sys
from pathlib import Path

root=Path(__file__).resolve().parent
commands=[[sys.executable,'-m','unittest','discover','-s','tests','-p','test_*.py'],
          ['node','tests/test_review_v94.cjs'],['node','tests/test_observation_ranking.cjs']]
for command in commands:
    result=subprocess.run(command,cwd=root)
    if result.returncode:sys.exit(result.returncode)
print('V94 offline release suite passed. No live AI or strategy-performance validation claimed.')

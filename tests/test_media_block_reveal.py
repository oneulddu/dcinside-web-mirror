from pathlib import Path
import shutil
import subprocess

import pytest


def test_media_block_reveal_state_transitions_execute_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the media reveal state-machine test")

    test_script = Path(__file__).parent / "javascript" / "media_block_reveal.test.cjs"
    completed = subprocess.run(
        [node, str(test_script)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert "media_block_reveal_state_machine=passed" in completed.stdout

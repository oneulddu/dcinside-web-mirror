from pathlib import Path


WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "deploy.yml"


def test_deploy_is_limited_to_main_and_pins_tested_sha():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'" in workflow
    assert "TARGET_SHA='${GITHUB_SHA}' bash -s" in workflow
    assert 'git rev-parse origin/main)" != "${TARGET_SHA}"' in workflow
    assert 'git rev-parse HEAD)" = "${TARGET_SHA}"' in workflow
    assert 'git merge --ff-only "${TARGET_SHA}"' in workflow
    assert "git pull" not in workflow


def test_deploy_preserves_running_process_and_verifies_worktree_health():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "delete dc-mirror" not in workflow
    assert "reload ecosystem.config.js --only dc-mirror --update-env" in workflow
    assert workflow.count("git diff-index --quiet HEAD --") >= 3
    assert "--retry-connrefused" in workflow

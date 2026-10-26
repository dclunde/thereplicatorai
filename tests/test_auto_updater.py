from unittest.mock import MagicMock, patch

from src.core.auto_updater import (
    AutoUpdater,
    is_in_maintenance_window,
    resolve_git_binary,
)


def test_maintenance_window_matching():
    # Standard same-day window
    assert is_in_maintenance_window("04:30", "04:00", "05:00") is True
    assert is_in_maintenance_window("03:59", "04:00", "05:00") is False
    assert is_in_maintenance_window("05:01", "04:00", "05:00") is False

    # Cross midnight window (23:00 to 02:00)
    assert is_in_maintenance_window("23:30", "23:00", "02:00") is True
    assert is_in_maintenance_window("01:15", "23:00", "02:00") is True
    assert is_in_maintenance_window("12:00", "23:00", "02:00") is False

    # Empty or identical window allows all
    assert is_in_maintenance_window("12:00", "", "") is True
    assert is_in_maintenance_window("12:00", "04:00", "04:00") is True


def test_resolve_git_binary():
    git = resolve_git_binary()
    # On systems with git installed (or GithubDesktop), should return a string
    assert git is None or isinstance(git, str)


def test_auto_updater_dirty_working_tree_aborts():
    updater = AutoUpdater(config={"auto_updater": {"enabled": True}})
    updater.git_bin = "mock_git"

    with patch.object(updater, "is_dirty", return_value=True):
        success, msg = updater.apply_update(force=True)
        assert success is False
        assert "Dirty working directory" in msg


def test_auto_updater_busy_system_aborts():
    updater = AutoUpdater(config={"auto_updater": {"enabled": True}})
    updater.git_bin = "mock_git"

    with (
        patch.object(updater, "is_dirty", return_value=False),
        patch.object(updater, "check_for_updates", return_value=(True, "commit_old", "commit_new")),
        patch(
            "src.core.auto_updater.is_system_idle",
            return_value=(False, "Active job lease: CAD_generate"),
        ),
    ):
        success, msg = updater.apply_update(force=True)
        assert success is False
        assert "Active job lease" in msg


def test_auto_updater_successful_cycle():
    mock_supervisor = MagicMock()
    updater = AutoUpdater(
        config={"auto_updater": {"enabled": True, "branch": "main"}},
        supervisor=mock_supervisor,
    )
    updater.git_bin = "mock_git"

    with (
        patch.object(updater, "is_dirty", return_value=False),
        patch.object(updater, "check_for_updates", return_value=(True, "aaa1111", "bbb2222")),
        patch("src.core.auto_updater.is_system_idle", return_value=(True, "System is idle")),
        patch.object(updater, "run_git") as mock_run_git,
        patch("subprocess.run") as mock_subproc,
    ):
        # 1st call: git pull --ff-only -> returncode 0
        # 2nd call: git diff -> returns "src/core/utils.py"
        mock_run_git.side_effect = [
            (0, "Fast-forward", ""),
            (0, "src/core/utils.py", ""),
        ]

        success, msg = updater.apply_update(force=True)
        assert success is True
        assert "bbb2222" in msg
        mock_supervisor.restart_all.assert_called_once()

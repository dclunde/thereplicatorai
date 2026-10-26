import json
import sys
from unittest.mock import patch

from src.cli.supervisor import main


def test_cli_status(capsys):
    with patch.object(sys, "argv", ["supervisor", "--status"]):
        code = main()
        assert code == 0
        captured = capsys.readouterr().out
        assert "TEXT-TO-PRINTER SUPERVISOR STATUS" in captured
        assert "online_backend" in captured


def test_cli_status_json(capsys):
    with patch.object(sys, "argv", ["supervisor", "--status", "--json"]):
        code = main()
        assert code == 0
        captured = capsys.readouterr().out
        data = json.loads(captured)
        assert "online_backend" in data


def test_cli_restarts(capsys):
    with patch.object(sys, "argv", ["supervisor", "--restarts"]):
        code = main()
        assert code == 0
        captured = capsys.readouterr().out
        assert "RECENT LIFECYCLE & RESTART EVENTS" in captured


def test_cli_reset_breaker(capsys):
    with patch.object(sys, "argv", ["supervisor", "--reset-breaker", "telegram_bot"]):
        code = main()
        assert code == 0
        captured = capsys.readouterr().out
        assert "Circuit breaker reset for 'telegram_bot'" in captured

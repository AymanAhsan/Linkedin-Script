"""Tests for the non-interactive daily outreach command."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli


def test_daily_sequence_runs_every_stage_in_order(monkeypatch, capsys):
    calls = []

    async def stage(name):
        calls.append(name)

    monkeypatch.setattr(
        cli, "cmd_collect_prospects", lambda: stage("search")
    )
    monkeypatch.setattr(
        cli, "find_queued_prospects", lambda: stage("connect")
    )
    monkeypatch.setattr(cli, "check_replies", lambda: stage("check-replies"))
    monkeypatch.setattr(
        cli, "send_due_messages", lambda: stage("send-messages")
    )

    asyncio.run(cli.cmd_daily_sequence())

    assert calls == ["search", "connect", "check-replies", "send-messages"]
    assert "Daily sequence complete." in capsys.readouterr().out

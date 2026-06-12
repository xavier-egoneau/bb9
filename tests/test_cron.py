from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

from bb9.api.chat import ChatApiApp, ChatApiState
from bb9.cli.main import Cli, CliState
from bb9.core.cron import (
    CronHistoryPolicy,
    CronRetryPolicy,
    CronRunState,
    CronSpec,
    CronStateStore,
    build_cron_index,
    cron_intention_text,
    cron_is_due,
    cron_should_notify,
    discover_crons,
    due_crons,
    load_cron,
    load_enabled_crons,
    next_run_after,
    refresh_cron_index,
)
from bb9.core.models import AgentProfile
from bb9.core.sessions import AGENT_HOME_SOURCE, SessionStore


class FakeCronProvider:
    def complete(self, prompt: str, **_: object) -> str:
        return """
        {
          "operations": [
            {"op": "node.add", "content": "Cron dream consolidé", "scope": "global", "source": "cron"}
          ],
          "actions": [],
          "summary": "cron ok"
        }
        """


class FakeProviderCli(Cli):
    def build_provider_for_agent(self, agent: AgentProfile):
        return FakeCronProvider()


class CronArchiveTests(unittest.TestCase):
    def test_loads_once_cron_with_absolute_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = root / "provider-reminder"
            item.mkdir()
            item.joinpath("CRON.md").write_text(
                "# CRON.md\n\n"
                "## Résumé\n\nRelancer la décision provider.\n\n"
                "## Activation\n\nactive\n\n"
                "## Agent\n\ndefault\n\n"
                "## Mode\n\nonce\n\n"
                "## Schedule\n\nAt: 2026-05-28 14:00\nTimezone: Europe/Paris\n\n"
                "## Intention\n\nDemande si on tranche API key ou OAuth web.\n\n"
                "## Après exécution\n\nKeep: archived\nNotify: yes\n",
                encoding="utf-8",
            )

            cron = load_cron(root, "provider-reminder")

            self.assertEqual("provider-reminder", cron.name)
            self.assertEqual("active", cron.activation)
            self.assertEqual("once", cron.mode)
            self.assertEqual("2026-05-28 14:00", cron.at)
            self.assertEqual("Europe/Paris", cron.timezone)
            self.assertIn("API key", cron.intention)
            self.assertIn("Keep: archived", cron.after_execution)

    def test_loads_declared_retry_notification_and_history_policies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = root / "resilient"
            item.mkdir()
            item.joinpath("CRON.md").write_text(
                "# CRON.md\n\n"
                "## Activation\n\nactive\n\n"
                "## Mode\n\nrecurring\n\n"
                "## Schedule\n\nTime: 08:30\n\n"
                "## Retry\n\nAttempts: 2\nDelay: 10m\n\n"
                "## Notification\n\nMode: always\nChannel: local\n\n"
                "## History\n\nMode: summary\nLimit: 3\n",
                encoding="utf-8",
            )

            cron = load_cron(root, "resilient")

            self.assertEqual(2, cron.retry_policy.attempts)
            self.assertEqual(10, cron.retry_policy.delay_minutes)
            self.assertEqual("always", cron.notification_policy.mode)
            self.assertEqual("local", cron.notification_policy.channel)
            self.assertEqual("summary", cron.history_policy.mode)
            self.assertEqual(3, cron.history_policy.limit)
            self.assertTrue(cron_should_notify(cron, ok=True))

    def test_loads_internal_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = root / "nightly-dream"
            item.mkdir()
            item.joinpath("CRON.md").write_text(
                "# CRON.md\n\n"
                "## Activation\n\nactive\n\n"
                "## Mode\n\nrecurring\n\n"
                "## Schedule\n\nTime: 02:00\n\n"
                "## Command\n\n/dream run nightly\n",
                encoding="utf-8",
            )

            cron = load_cron(root, "nightly-dream")

            self.assertEqual("/dream run nightly", cron.command)
            self.assertIn("/dream run nightly", cron_intention_text(cron))

    def test_notification_policy_defaults_to_errors_only(self) -> None:
        cron = CronSpec(name="daily", body="")

        self.assertFalse(cron_should_notify(cron, ok=True))
        self.assertTrue(cron_should_notify(cron, ok=False))

    def test_loads_recurring_cron_with_weekday_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = root / "morning-briefing"
            item.mkdir()
            item.joinpath("CRON.md").write_text(
                "# CRON.md\n\n"
                "## Résumé\n\nBriefing du matin.\n\n"
                "## Activation\n\nactive\n\n"
                "## Mode\n\nrecurring\n\n"
                "## Schedule\n\nTime: 08:30\nDays: weekdays\nTimezone: Europe/Paris\n\n"
                "## Limites\n\n- Lecture seule.\n",
                encoding="utf-8",
            )

            cron = load_cron(root, "morning-briefing")

            self.assertEqual("recurring", cron.mode)
            self.assertEqual("08:30", cron.time)
            self.assertEqual(
                ("monday", "tuesday", "wednesday", "thursday", "friday"),
                cron.days,
            )
            self.assertEqual("default", cron.agent)
            self.assertIn("Lecture seule", cron.limits)

    def test_mode_can_be_inferred_from_schedule_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = root / "ping"
            item.mkdir()
            item.joinpath("CRON.md").write_text(
                "# CRON.md\n\n## Schedule\n\nTime: 12:00\nDays: monday, friday\n",
                encoding="utf-8",
            )

            cron = load_cron(root, "ping")

            self.assertEqual("recurring", cron.mode)
            self.assertEqual(("monday", "friday"), cron.days)
            self.assertEqual("paused", cron.activation)

    def test_recurring_cron_without_days_defaults_to_daily(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = root / "daily-ping"
            item.mkdir()
            item.joinpath("CRON.md").write_text(
                "# CRON.md\n\n## Mode\n\nrecurring\n\n## Schedule\n\nTime: 12:00\n",
                encoding="utf-8",
            )

            cron = load_cron(root, "daily-ping")

            self.assertEqual(
                (
                    "monday",
                    "tuesday",
                    "wednesday",
                    "thursday",
                    "friday",
                    "saturday",
                    "sunday",
                ),
                cron.days,
            )

    def test_discovers_enabled_crons_and_builds_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = root / "active-one"
            active.mkdir()
            active.joinpath("CRON.md").write_text(
                "# CRON.md\n\n## Résumé\n\nActif.\n\n## Activation\n\nactive\n\n## Schedule\n\nAt: 2026-05-28 14:00\n",
                encoding="utf-8",
            )
            paused = root / "paused-one"
            paused.mkdir()
            paused.joinpath("CRON.md").write_text(
                "# CRON.md\n\n## Résumé\n\nPause.\n\n## Activation\n\npaused\n",
                encoding="utf-8",
            )

            self.assertEqual(["active-one", "paused-one"], discover_crons(root))
            self.assertEqual(
                ("active-one",),
                tuple(cron.name for cron in load_enabled_crons(root)),
            )
            crons = tuple(load_cron(root, name) for name in discover_crons(root))
            self.assertIn("`active-one`", build_cron_index(crons))

    def test_refresh_cron_index_writes_without_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = root / "daily"
            item.mkdir()
            item.joinpath("CRON.md").write_text(
                "# CRON.md\n\n## Résumé\n\nDaily.\n\n## Mode\n\nrecurring\n\n## Schedule\n\nTime: 09:00\nDays: daily\n",
                encoding="utf-8",
            )

            index = refresh_cron_index(root)

            self.assertIn("`daily`", index)
            self.assertNotIn("last_run", index)
            self.assertTrue((root / "INDEX.md").is_file())

    def test_once_cron_is_due_once_after_absolute_schedule(self) -> None:
        cron = CronSpec(
            name="reminder",
            body="",
            activation="active",
            mode="once",
            at="2026-05-28 14:00",
        )

        self.assertFalse(cron_is_due(cron, datetime(2026, 5, 28, 13, 59)))
        self.assertTrue(cron_is_due(cron, datetime(2026, 5, 28, 14, 0)))
        self.assertFalse(
            cron_is_due(
                cron,
                datetime(2026, 5, 28, 14, 1),
                CronRunState(last_run="2026-05-28 14:00"),
            )
        )
        self.assertEqual(
            datetime(2026, 5, 28, 14, 0),
            next_run_after(cron, datetime(2026, 5, 28, 13, 59)),
        )
        self.assertIsNone(next_run_after(cron, datetime(2026, 5, 28, 14, 0)))

    def test_recurring_cron_is_due_on_configured_day_and_time(self) -> None:
        cron = CronSpec(
            name="briefing",
            body="",
            activation="active",
            mode="recurring",
            time="08:30",
            days=("monday", "wednesday"),
        )

        self.assertFalse(cron_is_due(cron, datetime(2026, 5, 25, 8, 29)))
        self.assertTrue(cron_is_due(cron, datetime(2026, 5, 25, 8, 30)))
        self.assertFalse(cron_is_due(cron, datetime(2026, 5, 26, 9, 0)))

    def test_recurring_cron_uses_last_run_to_prevent_duplicate_run(self) -> None:
        cron = CronSpec(
            name="briefing",
            body="",
            activation="active",
            mode="recurring",
            time="08:30",
            days=("monday",),
        )

        self.assertFalse(
            cron_is_due(
                cron,
                datetime(2026, 5, 25, 9, 0),
                CronRunState(last_run="2026-05-25 08:30"),
            )
        )
        self.assertTrue(
            cron_is_due(
                cron,
                datetime(2026, 6, 1, 9, 0),
                CronRunState(last_run="2026-05-25 08:30"),
            )
        )

    def test_next_run_after_skips_to_next_configured_day(self) -> None:
        cron = CronSpec(
            name="weekly",
            body="",
            activation="active",
            mode="recurring",
            time="10:00",
            days=("wednesday",),
        )

        self.assertEqual(
            datetime(2026, 5, 27, 10, 0),
            next_run_after(cron, datetime(2026, 5, 25, 12, 0)),
        )
        self.assertEqual(
            datetime(2026, 6, 3, 10, 0),
            next_run_after(cron, datetime(2026, 5, 27, 10, 0)),
        )

    def test_monthly_cron_is_due_on_configured_day(self) -> None:
        cron = CronSpec(
            name="monthly",
            body="",
            activation="active",
            mode="recurring",
            frequency="monthly",
            time="08:30",
            day_of_month=15,
        )

        self.assertFalse(cron_is_due(cron, datetime(2026, 5, 15, 8, 29)))
        self.assertTrue(cron_is_due(cron, datetime(2026, 5, 15, 8, 30)))
        self.assertFalse(cron_is_due(cron, datetime(2026, 5, 16, 8, 30)))
        self.assertEqual(
            datetime(2026, 6, 15, 8, 30),
            next_run_after(cron, datetime(2026, 5, 16, 8, 30)),
        )

    def test_minutely_cron_uses_interval_since_last_run(self) -> None:
        cron = CronSpec(
            name="heartbeat",
            body="",
            activation="active",
            mode="recurring",
            frequency="minutely",
            interval_minutes=15,
        )

        self.assertTrue(cron_is_due(cron, datetime(2026, 5, 25, 8, 0)))
        self.assertFalse(
            cron_is_due(
                cron,
                datetime(2026, 5, 25, 8, 14),
                CronRunState(last_run="2026-05-25 08:00"),
            )
        )
        self.assertTrue(
            cron_is_due(
                cron,
                datetime(2026, 5, 25, 8, 15),
                CronRunState(last_run="2026-05-25 08:00"),
            )
        )
        self.assertEqual(
            datetime(2026, 5, 25, 8, 15),
            next_run_after(cron, datetime(2026, 5, 25, 8, 14), CronRunState(last_run="2026-05-25 08:00")),
        )

    def test_hourly_cron_parses_every_as_hours(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = root / "sync"
            item.mkdir()
            item.joinpath("CRON.md").write_text(
                "# CRON.md\n\n"
                "## Activation\n\nactive\n\n"
                "## Mode\n\nrecurring\n\n"
                "## Schedule\n\nFrequency: hourly\nEvery: 2\n",
                encoding="utf-8",
            )
            cron = load_cron(root, "sync")

            self.assertEqual("hourly", cron.frequency)
            self.assertEqual(120, cron.interval_minutes)
            self.assertFalse(
                cron_is_due(
                    cron,
                    datetime(2026, 5, 25, 9, 59),
                    CronRunState(last_run="2026-05-25 08:00"),
                )
            )
            self.assertTrue(
                cron_is_due(
                    cron,
                    datetime(2026, 5, 25, 10, 0),
                    CronRunState(last_run="2026-05-25 08:00"),
                )
            )

    def test_yearly_cron_is_due_on_configured_month_and_day(self) -> None:
        cron = CronSpec(
            name="yearly",
            body="",
            activation="active",
            mode="recurring",
            frequency="yearly",
            time="09:00",
            month=6,
            day_of_month=12,
        )

        self.assertFalse(cron_is_due(cron, datetime(2026, 6, 12, 8, 59)))
        self.assertTrue(cron_is_due(cron, datetime(2026, 6, 12, 9, 0)))
        self.assertFalse(cron_is_due(cron, datetime(2026, 6, 13, 9, 0)))
        self.assertEqual(
            datetime(2027, 6, 12, 9, 0),
            next_run_after(cron, datetime(2026, 6, 13, 9, 0)),
        )

    def test_due_crons_filters_activation_and_runtime_lock(self) -> None:
        ready = CronSpec(
            name="ready",
            body="",
            activation="active",
            mode="recurring",
            time="08:30",
        )
        paused = CronSpec(
            name="paused",
            body="",
            activation="paused",
            mode="recurring",
            time="08:30",
        )
        locked = CronSpec(
            name="locked",
            body="",
            activation="active",
            mode="recurring",
            time="08:30",
        )

        crons = due_crons(
            (ready, paused, locked),
            datetime(2026, 5, 25, 9, 0),
            {"locked": CronRunState(locked=True)},
        )

        self.assertEqual(("ready",), tuple(cron.name for cron in crons))

    def test_timezone_aware_once_cron_uses_declared_timezone(self) -> None:
        cron = CronSpec(
            name="paris-reminder",
            body="",
            activation="active",
            mode="once",
            at="2026-05-28 14:00",
            timezone="Europe/Paris",
        )

        self.assertFalse(
            cron_is_due(cron, datetime(2026, 5, 28, 11, 59, tzinfo=UTC))
        )
        self.assertTrue(
            cron_is_due(cron, datetime(2026, 5, 28, 12, 0, tzinfo=UTC))
        )

    def test_cron_state_store_persists_runtime_state_outside_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cron-state.json"
            store = CronStateStore(path)

            store.record_run("daily", datetime(2026, 5, 25, 8, 30))
            store.record_error("weekly", "provider unavailable")
            store.set_locked("weekly", True)

            states = store.load()
            raw = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual("2026-05-25T08:30:00", states["daily"].last_run)
            self.assertEqual("provider unavailable", states["weekly"].last_error)
            self.assertTrue(states["weekly"].locked)
            self.assertIn("crons", raw)
            self.assertNotIn("CRON.md", path.read_text(encoding="utf-8"))

    def test_retry_policy_schedules_retry_without_rewriting_cron_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = root / "daily"
            item.mkdir()
            item.joinpath("CRON.md").write_text(
                "# CRON.md\n\n"
                "## Activation\n\nactive\n\n"
                "## Mode\n\nrecurring\n\n"
                "## Schedule\n\nTime: 08:30\nDays: daily\n\n"
                "## Retry\n\nAttempts: 2\nDelay: 10m\n",
                encoding="utf-8",
            )
            cron = load_cron(root, "daily")
            store = CronStateStore(root / "state.json")

            state = store.record_error(
                "daily",
                "provider unavailable",
                datetime(2026, 5, 25, 8, 31),
                cron.retry_policy,
                cron.history_policy,
            )

            self.assertEqual(1, state.failure_count)
            self.assertEqual("2026-05-25T08:41:00", state.retry_at)
            self.assertFalse(cron_is_due(cron, datetime(2026, 5, 25, 8, 40), state))
            self.assertTrue(cron_is_due(cron, datetime(2026, 5, 25, 8, 41), state))
            self.assertEqual(
                datetime(2026, 5, 25, 8, 41),
                next_run_after(cron, datetime(2026, 5, 25, 8, 40), state),
            )
            self.assertNotIn("retryAt", (item / "CRON.md").read_text(encoding="utf-8"))

    def test_history_policy_keeps_only_configured_number_of_runtime_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CronStateStore(Path(tmp) / "state.json")
            history = CronHistoryPolicy(limit=2)

            store.record_run("daily", datetime(2026, 5, 25, 8, 30), "first", history)
            store.record_error(
                "daily",
                "second",
                datetime(2026, 5, 26, 8, 30),
                CronRetryPolicy(),
                history,
            )
            state = store.record_run(
                "daily",
                datetime(2026, 5, 27, 8, 30),
                "third",
                history,
            )

            self.assertEqual(2, len(state.history))
            self.assertEqual(("second", "third"), tuple(record.summary for record in state.history))
            self.assertEqual(0, state.failure_count)
            self.assertEqual("", state.retry_at)

    def test_cli_cron_due_command_uses_markdown_archives_and_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crons = root / "cron"
            item = crons / "daily"
            item.mkdir(parents=True)
            item.joinpath("CRON.md").write_text(
                "# CRON.md\n\n"
                "## Résumé\n\nDaily.\n\n"
                "## Activation\n\nactive\n\n"
                "## Mode\n\nrecurring\n\n"
                "## Schedule\n\nTime: 00:00\nDays: daily\n",
                encoding="utf-8",
            )
            cli = Cli(
                CliState(
                    agents_dir=root / "agents",
                    skills_dir=root / "skills",
                    tools_dir=root / "tools",
                    crons_dir=crons,
                    cron_state_path=root / "cron-state.json",
                )
            )
            output = io.StringIO()

            with redirect_stdout(output):
                self.assertTrue(cli.cmd_cron("due"))

            self.assertIn("due.... daily", output.getvalue())

    def test_cron_can_run_internal_dream_command_without_agent_intention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents" / "default"
            agents.mkdir(parents=True)
            agents.joinpath("IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            dreams = root / "dreams"
            nightly = dreams / "nightly"
            nightly.mkdir(parents=True)
            nightly.joinpath("DREAM.md").write_text(
                "# DREAM.md\n\n## Activation\n\nactive\n\n## Agent\n\ndefault\n",
                encoding="utf-8",
            )
            cli = FakeProviderCli(
                CliState(
                    agents_dir=root / "agents",
                    skills_dir=root / "skills",
                    tools_dir=root / "tools",
                    dreams_dir=dreams,
                    memory_path=root / "memory.db",
                    session_store_path=root / "sessions.db",
                )
            )
            cron = CronSpec(
                name="nightly-dream",
                body="",
                activation="active",
                mode="recurring",
                command="/dream run nightly",
            )
            store = CronStateStore(root / "cron-state.json")
            output = io.StringIO()

            with redirect_stdout(output):
                cli.run_due_cron(cron, store, datetime(2026, 5, 25, 2, 0))

            self.assertIn("dream.. ok", output.getvalue())
            self.assertEqual("cron ok", store.get("nightly-dream").history[-1].summary)

    def test_cron_agent_intention_is_recorded_in_agent_home_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent_dir = root / "agents" / "default"
            agent_dir.mkdir(parents=True)
            agent_dir.joinpath("IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cli = FakeProviderCli(
                CliState(
                    agents_dir=root / "agents",
                    skills_dir=root / "skills",
                    tools_dir=root / "tools",
                    session_store_path=root / "sessions.db",
                )
            )
            previous_session_id = cli.state.session.id
            cron = CronSpec(
                name="veille",
                body="",
                activation="active",
                mode="recurring",
                agent="default",
                intention="Fais la veille.",
            )
            store = CronStateStore(root / "cron-state.json")

            with redirect_stdout(io.StringIO()):
                cli.run_due_cron(cron, store, datetime(2026, 5, 25, 8, 0))

            self.assertEqual(previous_session_id, cli.state.session.id)
            session_store = SessionStore(root / "sessions.db")
            try:
                home = session_store.get("agent-home:default")
            finally:
                session_store.close()
            self.assertIsNotNone(home)
            assert home is not None
            self.assertEqual(AGENT_HOME_SOURCE, home.source)
            self.assertIsNone(home.project_path)
            self.assertEqual(["user", "assistant"], [message.role for message in home.messages])
            self.assertEqual("/cron tick veille", home.messages[0].content)
            self.assertIn("cron ok", home.messages[1].content)

    def test_web_routine_scheduler_records_due_routine_in_agent_home_session(self) -> None:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            crons = root / "cron"
            agents = root / "agents" / "default"
            skills = root / "skills"
            tools = root / "tools"
            workspace.mkdir()
            agents.mkdir(parents=True)
            skills.mkdir()
            tools.mkdir()
            agents.joinpath("IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            item = crons / "morning"
            item.mkdir(parents=True)
            item.joinpath("CRON.md").write_text(
                "# CRON.md\n\n"
                "## Résumé\n\nBriefing.\n\n"
                "## Activation\n\nactive\n\n"
                "## Agent\n\ndefault\n\n"
                "## Mode\n\nrecurring\n\n"
                "## Schedule\n\nTime: 08:30\nTimezone: Europe/Paris\n\n"
                "## Intention\n\nFaire le briefing.\n",
                encoding="utf-8",
            )
            app = ChatApiApp(
                ChatApiState(
                    provider_kind="echo",
                    agents_dir=root / "agents",
                    skills_dir=skills,
                    tools_dir=tools,
                    crons_dir=crons,
                    cron_state_path=root / "cron-state.json",
                    session_store_path=root / "sessions.db",
                    visible_history_path=root / "history.db",
                    active_project_path=str(workspace),
                )
            )

            try:
                os.chdir(workspace)
                result = app.run_due_routines(now=datetime(2026, 5, 25, 8, 31))
            finally:
                os.chdir(cwd)

            self.assertEqual(1, result["ran"])
            self.assertEqual("2026-05-25T08:31:00", CronStateStore(root / "cron-state.json").get("morning").last_run)
            session_store = SessionStore(root / "sessions.db")
            try:
                home = session_store.get("agent-home:default")
            finally:
                session_store.close()
            self.assertIsNotNone(home)
            assert home is not None
            self.assertEqual(AGENT_HOME_SOURCE, home.source)
            self.assertEqual("/cron tick morning", home.messages[0].content)
            self.assertIn("Faire le briefing", home.messages[1].content)

if __name__ == "__main__":
    unittest.main()

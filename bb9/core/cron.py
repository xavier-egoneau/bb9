"""Markdown cron archive discovery and loading."""

from __future__ import annotations

import calendar
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .archives import ArchiveNotFoundError, MarkdownArchive, discover_archives, load_archive
from .markdown import extract_section
from .paths import bb9_home

CRON_FILE = "CRON.md"
INDEX_FILE = "INDEX.md"
STATE_FILE = "cron-state.json"

CronMode = Literal["once", "recurring"]
CronActivation = Literal["active", "paused"]
CronNotifyMode = Literal["none", "errors", "always"]
CronHistoryMode = Literal["none", "summary"]
CronFrequency = Literal["minutely", "hourly", "daily", "weekly", "monthly", "yearly"]

DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
DAY_ALIASES = {
    "daily": DAY_NAMES,
    "everyday": DAY_NAMES,
    "weekdays": DAY_NAMES[:5],
    "weekday": DAY_NAMES[:5],
    "weekend": DAY_NAMES[5:],
    "weekends": DAY_NAMES[5:],
}
MONTH_NAMES = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
MONTH_ALIASES = {
    "janvier": 1,
    "fevrier": 2,
    "février": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "août": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
    "décembre": 12,
}


@dataclass(frozen=True)
class CronRetryPolicy:
    attempts: int = 0
    delay_minutes: int = 0


@dataclass(frozen=True)
class CronNotificationPolicy:
    mode: CronNotifyMode = "errors"
    channel: str = "local"


@dataclass(frozen=True)
class CronHistoryPolicy:
    mode: CronHistoryMode = "summary"
    limit: int = 20


@dataclass(frozen=True)
class CronSpec:
    name: str
    body: str
    summary: str = ""
    activation: CronActivation = "paused"
    agent: str = "default"
    mode: CronMode = "once"
    frequency: CronFrequency = "daily"
    interval_minutes: int = 0
    at: str = ""
    time: str = ""
    days: tuple[str, ...] = ()
    day_of_month: int = 0
    month: int = 0
    timezone: str = ""
    command: str = ""
    intention: str = ""
    limits: str = ""
    after_execution: str = ""
    notification: str = ""
    retry_policy: CronRetryPolicy = field(default_factory=CronRetryPolicy)
    notification_policy: CronNotificationPolicy = field(default_factory=CronNotificationPolicy)
    history_policy: CronHistoryPolicy = field(default_factory=CronHistoryPolicy)

    def as_index_line(self) -> str:
        summary = self.summary or "-"
        schedule = self.at if self.mode == "once" else _schedule_label(self)
        parts = [
            f"- `{self.name}` ({self.activation}, {self.mode}) : {summary}",
            f"  Agent: {self.agent}",
        ]
        if schedule:
            parts.append(f"  Schedule: {schedule}")
        return "\n".join(parts)


@dataclass(frozen=True)
class CronRunRecord:
    time: str
    ok: bool
    summary: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "time": self.time,
            "ok": self.ok,
            "summary": self.summary,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> CronRunRecord:
        return CronRunRecord(
            time=str(data.get("time") or ""),
            ok=bool(data.get("ok") or False),
            summary=str(data.get("summary") or ""),
        )


@dataclass(frozen=True)
class CronRunState:
    last_run: str = ""
    last_error: str = ""
    locked: bool = False
    failure_count: int = 0
    retry_at: str = ""
    history: tuple[CronRunRecord, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "lastRun": self.last_run,
            "lastError": self.last_error,
            "locked": self.locked,
            "failureCount": self.failure_count,
            "retryAt": self.retry_at,
            "history": [record.to_dict() for record in self.history],
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> CronRunState:
        return CronRunState(
            last_run=str(data.get("lastRun") or data.get("last_run") or ""),
            last_error=str(data.get("lastError") or data.get("last_error") or ""),
            locked=bool(data.get("locked") or False),
            failure_count=_int_value(str(data.get("failureCount") or data.get("failure_count") or ""), 0),
            retry_at=str(data.get("retryAt") or data.get("retry_at") or ""),
            history=tuple(
                CronRunRecord.from_dict(item)
                for item in data.get("history", ())
                if isinstance(item, dict)
            ),
        )


class CronStateStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_cron_state_path()

    def load(self) -> dict[str, CronRunState]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        crons = raw.get("crons", raw)
        if not isinstance(crons, dict):
            return {}
        return {
            str(name): CronRunState.from_dict(value)
            for name, value in crons.items()
            if isinstance(value, dict)
        }

    def save(self, states: Mapping[str, CronRunState]) -> None:
        payload = {
            "crons": {
                name: state.to_dict()
                for name, state in sorted(states.items())
                if isinstance(state, CronRunState)
            }
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def get(self, name: str) -> CronRunState:
        return self.load().get(name, CronRunState())

    def update(self, name: str, state: CronRunState) -> CronRunState:
        states = self.load()
        states[name] = state
        self.save(states)
        return state

    def record_run(
        self,
        name: str,
        when: datetime,
        summary: str = "",
        history_policy: CronHistoryPolicy | None = None,
    ) -> CronRunState:
        current = self.get(name)
        history = _history_with(current, when, True, summary, history_policy)
        return self.update(
            name,
            CronRunState(
                last_run=when.isoformat(),
                last_error="",
                locked=current.locked,
                failure_count=0,
                retry_at="",
                history=history,
            ),
        )

    def record_error(
        self,
        name: str,
        error: str,
        when: datetime | None = None,
        retry_policy: CronRetryPolicy | None = None,
        history_policy: CronHistoryPolicy | None = None,
    ) -> CronRunState:
        current = self.get(name)
        failed_at = when or datetime.now().astimezone()
        retry_policy = retry_policy or CronRetryPolicy()
        failure_count = current.failure_count + 1
        retry_at = ""
        if failure_count <= retry_policy.attempts:
            retry_at = (failed_at + timedelta(minutes=retry_policy.delay_minutes)).isoformat()
        history = _history_with(current, failed_at, False, error, history_policy)
        return self.update(
            name,
            CronRunState(
                last_run=failed_at.isoformat(),
                last_error=error.strip(),
                locked=current.locked,
                failure_count=failure_count,
                retry_at=retry_at,
                history=history,
            ),
        )

    def set_locked(self, name: str, locked: bool) -> CronRunState:
        current = self.get(name)
        return self.update(
            name,
            CronRunState(
                last_run=current.last_run,
                last_error=current.last_error,
                locked=locked,
                failure_count=current.failure_count,
                retry_at=current.retry_at,
                history=current.history,
            ),
        )


def default_crons_dir() -> Path:
    return bb9_home() / "cron"


def default_cron_state_path() -> Path:
    return bb9_home() / STATE_FILE


def discover_crons(root: Path) -> list[str]:
    return discover_archives(root, CRON_FILE)


def load_cron(root: Path, name: str) -> CronSpec:
    try:
        archive = load_archive(root, name, CRON_FILE)
    except ArchiveNotFoundError as err:
        raise CronNotFoundError(f"Cron not found: {name}") from err
    return _cron_from_archive(archive)


def load_enabled_crons(root: Path) -> tuple[CronSpec, ...]:
    return tuple(
        cron
        for cron in (load_cron(root, name) for name in discover_crons(root))
        if cron.activation == "active"
    )


def build_cron_index(crons: tuple[CronSpec, ...]) -> str:
    lines = ["# Cron Index", ""]
    if crons:
        lines.extend(cron.as_index_line() for cron in crons)
    else:
        lines.append("Aucun cron configure.")
    return "\n".join(lines).strip() + "\n"


def refresh_cron_index(root: Path) -> str:
    crons = tuple(load_cron(root, name) for name in discover_crons(root))
    index = build_cron_index(crons)
    root.mkdir(parents=True, exist_ok=True)
    (root / INDEX_FILE).write_text(index, encoding="utf-8")
    return index


def cron_is_due(cron: CronSpec, now: datetime, state: CronRunState | None = None) -> bool:
    state = state or CronRunState()
    if cron.activation != "active" or state.locked:
        return False
    retry_at = _retry_at(cron, now, state)
    if retry_at is not None and _local_now(cron, now) >= retry_at:
        return True
    if cron.mode == "once":
        scheduled_at = _parse_at(cron, now)
        if scheduled_at is None or state.last_run:
            return False
        return _local_now(cron, now) >= scheduled_at
    if _effective_frequency(cron) in {"hourly", "minutely"}:
        return _interval_is_due(cron, now, state)
    scheduled_at = _scheduled_run_at_or_before(cron, now)
    if scheduled_at is None:
        return False
    last_run = _parse_runtime_datetime(state.last_run, scheduled_at, cron)
    return last_run is None or last_run < scheduled_at


def next_run_after(
    cron: CronSpec,
    now: datetime,
    state: CronRunState | None = None,
) -> datetime | None:
    state = state or CronRunState()
    if cron.activation != "active" or state.locked:
        return None
    local_now = _local_now(cron, now)
    retry_at = _retry_at(cron, now, state)
    if retry_at is not None and retry_at > local_now:
        return retry_at
    if cron.mode == "once":
        scheduled_at = _parse_at(cron, now)
        if scheduled_at is None or state.last_run or scheduled_at <= local_now:
            return None
        return scheduled_at
    if _effective_frequency(cron) in {"hourly", "minutely"}:
        return _next_interval_run_after(cron, local_now, state)
    for offset in range(370):
        candidate = _candidate_on_or_after(cron, local_now, offset)
        if candidate is not None and candidate > local_now:
            return candidate
    return None


def due_crons(
    crons: tuple[CronSpec, ...],
    now: datetime,
    state_by_name: Mapping[str, CronRunState] | None = None,
) -> tuple[CronSpec, ...]:
    state_by_name = state_by_name or {}
    return tuple(
        cron for cron in crons if cron_is_due(cron, now, state_by_name.get(cron.name))
    )


def cron_should_notify(cron: CronSpec, ok: bool) -> bool:
    mode = cron.notification_policy.mode
    if mode == "none":
        return False
    if mode == "always":
        return True
    return not ok


def cron_intention_text(cron: CronSpec) -> str:
    parts = [
        f"Cron BB9 déclenché: {cron.name}",
        f"Mode: {cron.mode}",
    ]
    if cron.summary.strip():
        parts.extend(["Résumé:", cron.summary.strip()])
    if cron.command.strip():
        parts.extend(["Command:", cron.command.strip()])
    if cron.intention.strip():
        parts.extend(["Intention:", cron.intention.strip()])
    if cron.limits.strip():
        parts.extend(["Limites:", cron.limits.strip()])
    if cron.after_execution.strip():
        parts.extend(["Après exécution:", cron.after_execution.strip()])
    if cron.notification.strip():
        parts.extend(["Notification:", cron.notification.strip()])
    return "\n\n".join(parts)


class CronNotFoundError(RuntimeError):
    pass


def _cron_from_archive(archive: MarkdownArchive) -> CronSpec:
    body = archive.body
    schedule = _section(body, "Schedule", "Planification")
    after_execution = _section(body, "Après exécution", "Apres execution", "After execution")
    notification = _section(body, "Notification")
    retry = _section(body, "Retry", "Relance")
    history = _section(body, "History", "Historique")
    mode = _normalize_mode(_first_value(body, "Mode"), schedule)
    days = _parse_days(_field_value(schedule, "Days", "Jours"))
    frequency = _parse_frequency(_field_value(schedule, "Frequency", "Frequence", "Fréquence"), days)
    if mode == "recurring" and not days and frequency in {"daily", "weekly"}:
        days = DAY_NAMES
    return CronSpec(
        name=archive.name,
        body=body,
        summary=_section(body, "Résumé", "Resume").replace("\n", " "),
        activation=_normalize_activation(_first_value(body, "Activation")),
        agent=_first_value(body, "Agent") or "default",
        mode=mode,
        frequency=frequency,
        interval_minutes=_parse_interval_minutes(schedule, frequency),
        at=_field_value(schedule, "At"),
        time=_field_value(schedule, "Time", "Heure"),
        days=days,
        day_of_month=_bounded_int(_field_value(schedule, "Day", "DayOfMonth", "Jour", "JourDuMois"), 0, 31),
        month=_parse_month(_field_value(schedule, "Month", "Mois")),
        timezone=_field_value(schedule, "Timezone", "Fuseau", "Fuseau horaire"),
        command=_first_value(body, "Command", "Commande"),
        intention=_section(body, "Intention"),
        limits=_section(body, "Limites", "Limits"),
        after_execution=after_execution,
        notification=notification,
        retry_policy=_parse_retry_policy(retry),
        notification_policy=_parse_notification_policy(notification, after_execution),
        history_policy=_parse_history_policy(history),
    )


def _section(markdown: str, *headings: str) -> str:
    for heading in headings:
        value = extract_section(markdown, heading)
        if value:
            return value
    return ""


def _first_value(markdown: str, *headings: str) -> str:
    section = _section(markdown, *headings)
    for line in section.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _field_value(markdown: str, *labels: str) -> str:
    labels_normalized = {_normalize_label(label) for label in labels}
    for line in markdown.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        if _normalize_label(key) in labels_normalized:
            return value.strip()
    return ""


def _normalize_mode(value: str, schedule: str) -> CronMode:
    normalized = _normalize_label(value)
    if normalized in {"recurring", "recurrent", "routine"}:
        return "recurring"
    if normalized in {"once", "one shot", "oneshot", "scheduled", "planned", "unitaire", "ponctuel"}:
        return "once"
    if _field_value(schedule, "Time", "Heure"):
        return "recurring"
    return "once"


def _normalize_activation(value: str) -> CronActivation:
    normalized = _normalize_label(value)
    if normalized in {"active", "enabled", "on", "oui", "yes"}:
        return "active"
    return "paused"


def _parse_days(value: str) -> tuple[str, ...]:
    normalized = _normalize_label(value.replace(",", " "))
    if not normalized:
        return ()
    days: list[str] = []
    for token in normalized.split():
        expanded = DAY_ALIASES.get(token, (token,))
        for day in expanded:
            if day in DAY_NAMES and day not in days:
                days.append(day)
    return tuple(days)


def _parse_frequency(value: str, days: tuple[str, ...]) -> CronFrequency:
    normalized = _normalize_label(value)
    if normalized in {"minute", "minutes", "minutely", "min", "mins"}:
        return "minutely"
    if normalized in {"hour", "hours", "hourly", "heure", "heures"}:
        return "hourly"
    if normalized in {"year", "yearly", "annual", "annuel", "annee", "an"}:
        return "yearly"
    if normalized in {"month", "monthly", "mensuel", "mois"}:
        return "monthly"
    if normalized in {"week", "weekly", "hebdo", "hebdomadaire", "semaine", "semaines"}:
        return "weekly"
    if normalized in {"day", "daily", "quotidien", "jour", "jours"}:
        return "daily"
    if days and days != DAY_NAMES:
        return "weekly"
    return "daily"


def _parse_interval_minutes(markdown: str, frequency: CronFrequency) -> int:
    if frequency not in {"hourly", "minutely"}:
        return 0
    value = _field_value(markdown, "Every", "Interval", "EveryMinutes", "IntervalMinutes", "ToutesLes")
    normalized = _normalize_label(value)
    amount = _int_value(normalized, 1)
    if frequency == "hourly":
        if "minute" in normalized or normalized.endswith("m"):
            return max(1, _duration_minutes(normalized, amount))
        if "hour" in normalized or "heure" in normalized or normalized.endswith("h"):
            return max(1, _duration_minutes(normalized, amount * 60))
        return max(1, amount) * 60
    return max(1, _duration_minutes(normalized, amount))


def _parse_month(value: str) -> int:
    normalized = _normalize_label(value)
    if not normalized:
        return 0
    number = _int_value(normalized, 0)
    if 1 <= number <= 12:
        return number
    for index, name in enumerate(MONTH_NAMES, start=1):
        if normalized == name:
            return index
    return MONTH_ALIASES.get(normalized, 0)


def _bounded_int(value: str, default: int, maximum: int) -> int:
    number = _int_value(value, default)
    if number < 0:
        return default
    if maximum and number > maximum:
        return maximum
    return number


def _parse_retry_policy(markdown: str) -> CronRetryPolicy:
    attempts = _int_value(_field_value(markdown, "Attempts", "MaxAttempts", "Retries"), 0)
    delay = _duration_minutes(_field_value(markdown, "Delay", "DelayMinutes", "Delay minutes"), 0)
    return CronRetryPolicy(attempts=max(0, attempts), delay_minutes=max(0, delay))


def _parse_notification_policy(markdown: str, after_execution: str) -> CronNotificationPolicy:
    mode = _normalize_notification_mode(
        _field_value(markdown, "Mode", "On", "Notify")
        or _field_value(after_execution, "Notify")
    )
    channel = _field_value(markdown, "Channel", "Canal") or "local"
    return CronNotificationPolicy(mode=mode, channel=channel)


def _parse_history_policy(markdown: str) -> CronHistoryPolicy:
    mode = _normalize_history_mode(_field_value(markdown, "Mode", "Keep"))
    limit = _int_value(_field_value(markdown, "Limit", "Keep", "Runs"), 20)
    return CronHistoryPolicy(mode=mode, limit=max(0, limit))


def _normalize_notification_mode(value: str) -> CronNotifyMode:
    normalized = _normalize_label(value)
    if normalized in {"no", "non", "none", "off", "false", "never"}:
        return "none"
    if normalized in {"yes", "oui", "always", "all", "true"}:
        return "always"
    return "errors"


def _normalize_history_mode(value: str) -> CronHistoryMode:
    normalized = _normalize_label(value)
    if normalized in {"none", "no", "non", "off", "false", "never"}:
        return "none"
    return "summary"


def _int_value(value: str, default: int) -> int:
    stripped = value.strip()
    if not stripped:
        return default
    for token in stripped.replace(":", " ").split():
        if token.isdigit():
            return int(token)
        digits = "".join(char for char in token if char.isdigit())
        if digits:
            return int(digits)
    return default


def _duration_minutes(value: str, default: int) -> int:
    normalized = _normalize_label(value)
    if not normalized:
        return default
    minutes = _int_value(normalized, default)
    if "hour" in normalized or "heure" in normalized or normalized.endswith("h"):
        return minutes * 60
    return minutes


def _history_with(
    state: CronRunState,
    when: datetime,
    ok: bool,
    summary: str,
    policy: CronHistoryPolicy | None,
) -> tuple[CronRunRecord, ...]:
    policy = policy or CronHistoryPolicy()
    if policy.mode == "none" or policy.limit <= 0:
        return ()
    record = CronRunRecord(
        time=when.isoformat(),
        ok=ok,
        summary=" ".join(summary.split())[:500],
    )
    return (*state.history, record)[-policy.limit:]


def _retry_at(cron: CronSpec, now: datetime, state: CronRunState) -> datetime | None:
    if not state.retry_at or state.failure_count <= 0:
        return None
    if state.failure_count > cron.retry_policy.attempts:
        return None
    return _parse_runtime_datetime(state.retry_at, _local_now(cron, now), cron)


def _normalize_label(text: str) -> str:
    replacements = str.maketrans("àâäéèêëîïôöùûüç", "aaaeeeeiioouuuc")
    clean = text.lower().translate(replacements).replace("-", " ").replace("_", " ")
    return " ".join(clean.split())


def _parse_at(cron: CronSpec, now: datetime) -> datetime | None:
    parsed = _parse_runtime_datetime(cron.at, _local_now(cron, now), cron)
    return parsed


def _parse_runtime_datetime(value: str, reference: datetime, cron: CronSpec) -> datetime | None:
    if not value:
        return None
    normalized = value.strip().replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return _compatible_datetime(parsed, reference, cron)


def _compatible_datetime(value: datetime, reference: datetime, cron: CronSpec) -> datetime:
    if reference.tzinfo is None:
        return value.replace(tzinfo=None)
    if value.tzinfo is None:
        zone = _zone(cron.timezone)
        return value.replace(tzinfo=zone or reference.tzinfo)
    return value.astimezone(reference.tzinfo)


def _parse_time(value: str) -> time | None:
    if not value:
        return None
    try:
        parsed = time.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.replace(tzinfo=None)
    return parsed


def _local_now(cron: CronSpec, now: datetime) -> datetime:
    zone = _zone(cron.timezone)
    if zone is None or now.tzinfo is None:
        return now
    return now.astimezone(zone)


def _zone(name: str) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return None


def _scheduled_run_at_or_before(cron: CronSpec, now: datetime) -> datetime | None:
    local_now = _local_now(cron, now)
    if _effective_frequency(cron) in {"monthly", "yearly"}:
        candidate = _candidate_for_date(cron, local_now.date(), local_now)
        return candidate if candidate is not None and candidate <= local_now else None
    run_time = _parse_time(cron.time)
    if run_time is None:
        return None
    days = cron.days or DAY_NAMES
    candidate_date = local_now.date()
    if _day_name(candidate_date) not in days:
        return None
    candidate = _datetime_on(candidate_date, run_time, local_now)
    if candidate <= local_now:
        return candidate
    return None


def _interval_is_due(cron: CronSpec, now: datetime, state: CronRunState) -> bool:
    local_now = _local_now(cron, now)
    interval = _interval_delta(cron)
    if not state.last_run:
        return True
    last_run = _parse_runtime_datetime(state.last_run, local_now, cron)
    if last_run is None:
        return True
    return local_now >= last_run + interval


def _next_interval_run_after(cron: CronSpec, local_now: datetime, state: CronRunState) -> datetime | None:
    interval = _interval_delta(cron)
    if not state.last_run:
        return local_now
    last_run = _parse_runtime_datetime(state.last_run, local_now, cron)
    if last_run is None:
        return local_now
    candidate = last_run + interval
    return candidate if candidate > local_now else local_now


def _interval_delta(cron: CronSpec) -> timedelta:
    minutes = cron.interval_minutes
    if minutes <= 0:
        minutes = 60 if _effective_frequency(cron) == "hourly" else 1
    return timedelta(minutes=minutes)


def _candidate_on_or_after(cron: CronSpec, local_now: datetime, offset: int) -> datetime | None:
    candidate_date = local_now.date() + timedelta(days=offset)
    return _candidate_for_date(cron, candidate_date, local_now)


def _candidate_for_date(cron: CronSpec, candidate_date: date, reference: datetime) -> datetime | None:
    run_time = _parse_time(cron.time)
    if run_time is None:
        return None
    frequency = _effective_frequency(cron)
    if frequency == "yearly":
        month = cron.month or 1
        day = cron.day_of_month or 1
        if candidate_date.month != month or candidate_date.day != day:
            return None
    elif frequency == "monthly":
        day = cron.day_of_month or 1
        if candidate_date.day != min(day, calendar.monthrange(candidate_date.year, candidate_date.month)[1]):
            return None
    else:
        days = cron.days or DAY_NAMES
        if frequency == "weekly" and _day_name(candidate_date) not in days:
            return None
    return _datetime_on(candidate_date, run_time, reference)


def _schedule_label(cron: CronSpec) -> str:
    if cron.mode == "once":
        return cron.at or "-"
    frequency = _effective_frequency(cron)
    if frequency == "minutely":
        return f"every {cron.interval_minutes or 1}m"
    if frequency == "hourly":
        minutes = cron.interval_minutes or 60
        if minutes % 60 == 0:
            return f"every {minutes // 60}h"
        return f"every {minutes}m"
    if frequency == "yearly":
        return f"{cron.time or '-'} yearly month={cron.month or 1} day={cron.day_of_month or 1}"
    if frequency == "monthly":
        return f"{cron.time or '-'} monthly day={cron.day_of_month or 1}"
    if frequency == "weekly":
        days = ",".join(cron.days) if cron.days else "daily"
        return f"{cron.time or '-'} weekly {days}".strip()
    return f"{cron.time or '-'} daily"


def _effective_frequency(cron: CronSpec) -> CronFrequency:
    if cron.frequency == "daily" and cron.days and cron.days != DAY_NAMES:
        return "weekly"
    return cron.frequency


def _datetime_on(day: date, run_time: time, reference: datetime) -> datetime:
    candidate = datetime.combine(day, run_time)
    if reference.tzinfo is not None:
        return candidate.replace(tzinfo=reference.tzinfo)
    return candidate


def _day_name(day: date) -> str:
    return DAY_NAMES[day.weekday()]

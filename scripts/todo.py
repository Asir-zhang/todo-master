#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import pathlib
import tempfile
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


VERSION = 1
TZ_CN = timezone(timedelta(hours=8), name="Asia/Shanghai")
DEFAULT_TIMEZONE = "Asia/Shanghai"
DATE_FMT = "%Y-%m-%d"
MONTH_FMT = "%Y-%m"

STATUS_OPEN = "open"
STATUS_DONE = "done"
STATUS_CANCELED = "canceled"
STATUS_DELETED = "deleted"
ALL_STATUS = {STATUS_OPEN, STATUS_DONE, STATUS_CANCELED, STATUS_DELETED}

TYPE_LONG = "long"
TYPE_SHORT = "short"
ALL_TYPES = {TYPE_LONG, TYPE_SHORT}

BY_CREATED = "created"
BY_PLAN = "plan"
BY_DUE = "due"
ALL_BY = {BY_CREATED, BY_PLAN, BY_DUE}

DUE_OVERDUE = "overdue"
DUE_NOT_OVERDUE = "not-overdue"
DUE_ALL = "all"
ALL_DUE_STATE = {DUE_OVERDUE, DUE_NOT_OVERDUE, DUE_ALL}

ERR_VALIDATION = "ERR_VALIDATION"
ERR_NOT_FOUND = "ERR_NOT_FOUND"
ERR_STORAGE = "ERR_STORAGE"
ERR_CORRUPTION = "ERR_CORRUPTION"
ERR_NOT_INITIALIZED = "ERR_NOT_INITIALIZED"

EXIT_VALIDATION = 2
EXIT_NOT_FOUND = 3
EXIT_STORAGE = 4
EXIT_CORRUPTION = 5


class TodoError(Exception):
    def __init__(self, prefix: str, message: str, exit_code: int):
        super().__init__(message)
        self.prefix = prefix
        self.message = message
        self.exit_code = exit_code


class ValidationError(TodoError):
    def __init__(self, message: str):
        super().__init__(ERR_VALIDATION, message, EXIT_VALIDATION)


class NotFoundError(TodoError):
    def __init__(self, message: str):
        super().__init__(ERR_NOT_FOUND, message, EXIT_NOT_FOUND)


class StorageError(TodoError):
    def __init__(self, message: str):
        super().__init__(ERR_STORAGE, message, EXIT_STORAGE)


class CorruptionError(TodoError):
    def __init__(self, message: str):
        super().__init__(ERR_CORRUPTION, message, EXIT_CORRUPTION)


class NotInitializedError(TodoError):
    def __init__(self, message: str):
        super().__init__(ERR_NOT_INITIALIZED, message, EXIT_VALIDATION)


class StrictArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValidationError(message)


@dataclass
class StorePaths:
    skill_root: pathlib.Path
    data_dir: pathlib.Path
    index_file: pathlib.Path
    config_file: pathlib.Path

    @classmethod
    def from_script(cls) -> "StorePaths":
        script_path = pathlib.Path(__file__).resolve()
        skill_root = script_path.parent.parent
        config_file = skill_root / "config.json"
        data_dir = skill_root / "data"
        return cls(skill_root=skill_root, data_dir=data_dir, index_file=data_dir / "index.json", config_file=config_file)

    def with_data_dir(self, data_dir: pathlib.Path) -> "StorePaths":
        return StorePaths(
            skill_root=self.skill_root,
            data_dir=data_dir,
            index_file=data_dir / "index.json",
            config_file=self.config_file,
        )

    def month_file(self, month: str) -> pathlib.Path:
        return self.data_dir / f"todos-{month}.json"


def now_cn() -> datetime:
    return datetime.now(TZ_CN)


def now_cn_iso() -> str:
    return now_cn().isoformat(timespec="seconds")


def parse_day(value: str, field_name: str) -> date:
    try:
        return datetime.strptime(value, DATE_FMT).date()
    except ValueError as exc:
        raise ValidationError(f"{field_name} must be YYYY-MM-DD, got: {value}") from exc


def parse_iso_datetime(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CorruptionError(f"invalid datetime format for {field_name}: {value}") from exc
    if parsed.tzinfo is None:
        raise CorruptionError(f"datetime missing timezone for {field_name}: {value}")
    return parsed


def month_from_day(day_value: str, field_name: str) -> str:
    return parse_day(day_value, field_name).strftime(MONTH_FMT)


def month_from_created_at(created_at: str) -> str:
    created = parse_iso_datetime(created_at, "created_at").astimezone(TZ_CN)
    return created.strftime(MONTH_FMT)


def compute_archive_month(todo: Dict[str, Any]) -> str:
    todo_type = todo.get("type")
    if todo_type == TYPE_LONG and todo.get("due_date"):
        return month_from_day(todo["due_date"], "due_date")
    if todo.get("plan_date"):
        return month_from_day(todo["plan_date"], "plan_date")
    return month_from_created_at(todo["created_at"])


def make_id() -> str:
    now_ms_hex = hex(int(now_cn().timestamp() * 1000))[2:]
    return f"t_{now_ms_hex}_{uuid.uuid4().hex[:6]}"


def parse_json_object(raw_text: str, source_name: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise CorruptionError(f"{source_name} contains invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise CorruptionError(f"{source_name} must be a JSON object")
    return parsed


def atomic_write_json(path: pathlib.Path, data: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            delete=False,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as handle:
            tmp_path = pathlib.Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_path, path)
    except OSError as exc:
        raise StorageError(f"failed writing file: {path}") from exc


def default_config() -> Dict[str, Any]:
    return {"version": VERSION, "initialized": False, "data_dir": "", "timezone": DEFAULT_TIMEZONE}


def load_config(base_paths: StorePaths) -> Dict[str, Any]:
    if not base_paths.config_file.exists():
        return default_config()
    try:
        raw = base_paths.config_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise StorageError(f"failed reading file: {base_paths.config_file}") from exc
    cfg = parse_json_object(raw, "config.json")
    if not isinstance(cfg.get("version"), int):
        raise CorruptionError("config.json missing integer version")
    if not isinstance(cfg.get("initialized"), bool):
        raise CorruptionError("config.json missing boolean initialized")
    if not isinstance(cfg.get("data_dir"), str):
        raise CorruptionError("config.json missing string data_dir")
    if not isinstance(cfg.get("timezone"), str):
        raise CorruptionError("config.json missing string timezone")
    return cfg


def write_config(base_paths: StorePaths, cfg: Dict[str, Any]) -> None:
    atomic_write_json(base_paths.config_file, cfg)


def resolve_configured_data_dir(base_paths: StorePaths, cfg: Dict[str, Any]) -> pathlib.Path:
    configured = cfg.get("data_dir", "").strip()
    if configured:
        configured_path = pathlib.Path(configured)
        if not configured_path.is_absolute():
            raise CorruptionError("config.json data_dir must be absolute when non-empty")
        return configured_path
    return base_paths.skill_root / "data"


def init_guidance(base_paths: StorePaths) -> str:
    return (
        "skill is not initialized. Run one of:\n"
        f"  python3 {pathlib.Path(__file__).resolve()} init --default\n"
        f"  python3 {pathlib.Path(__file__).resolve()} init --data-dir /absolute/path"
    )


def ensure_initialized(base_paths: StorePaths, cfg: Dict[str, Any]) -> None:
    if not cfg.get("initialized", False):
        raise NotInitializedError(init_guidance(base_paths))


def ensure_index(paths: StorePaths) -> Dict[str, Any]:
    if not paths.index_file.exists():
        index = {
            "version": VERSION,
            "months": [],
            "id_map": {},
            "stats": {"open": 0, "done": 0, "canceled": 0, "deleted": 0, "updated_at": now_cn_iso()},
        }
        atomic_write_json(paths.index_file, index)
        return index

    try:
        raw = paths.index_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise StorageError(f"failed reading file: {paths.index_file}") from exc
    index = parse_json_object(raw, "index.json")

    if not isinstance(index.get("version"), int):
        raise CorruptionError("index.json missing integer version")
    if not isinstance(index.get("months"), list) or not all(isinstance(x, str) for x in index["months"]):
        raise CorruptionError("index.json invalid months")
    if not isinstance(index.get("id_map"), dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in index["id_map"].items()
    ):
        raise CorruptionError("index.json invalid id_map")
    if not isinstance(index.get("stats"), dict):
        raise CorruptionError("index.json invalid stats")

    for key in ("open", "done", "canceled", "deleted"):
        if not isinstance(index["stats"].get(key), int):
            raise CorruptionError(f"index.json stats missing int field: {key}")
    if not isinstance(index["stats"].get("updated_at"), str):
        raise CorruptionError("index.json stats.updated_at missing string")
    parse_iso_datetime(index["stats"]["updated_at"], "stats.updated_at")
    return index


def validate_todo_shape(todo: Any) -> None:
    if not isinstance(todo, dict):
        raise CorruptionError("todo item must be object")
    required_str = ["id", "title", "type", "status", "created_at", "updated_at", "archive_month"]
    for field in required_str:
        if not isinstance(todo.get(field), str) or not todo[field].strip():
            raise CorruptionError(f"todo missing valid string field: {field}")
    if todo["type"] not in ALL_TYPES:
        raise CorruptionError(f"invalid todo type: {todo['type']}")
    if todo["status"] not in ALL_STATUS:
        raise CorruptionError(f"invalid todo status: {todo['status']}")
    parse_iso_datetime(todo["created_at"], "created_at")
    parse_iso_datetime(todo["updated_at"], "updated_at")
    if not isinstance(todo.get("tags", []), list) or not all(isinstance(t, str) for t in todo.get("tags", [])):
        raise CorruptionError("todo.tags must be list[str]")
    if todo.get("note") is not None and not isinstance(todo["note"], str):
        raise CorruptionError("todo.note must be string or null")
    for field in ("plan_date", "due_date"):
        if todo.get(field) is not None:
            if not isinstance(todo[field], str):
                raise CorruptionError(f"todo.{field} must be string or null")
            parse_day(todo[field], field)
    if todo["type"] == TYPE_SHORT and not todo.get("plan_date"):
        raise CorruptionError("short todo missing plan_date")
    if len(todo["archive_month"]) != 7:
        raise CorruptionError("todo.archive_month must be YYYY-MM")


def ensure_month_file(paths: StorePaths, month: str) -> Dict[str, Any]:
    file_path = paths.month_file(month)
    if not file_path.exists():
        payload = {"version": VERSION, "month": month, "todos": []}
        atomic_write_json(file_path, payload)
        return payload

    try:
        raw = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StorageError(f"failed reading file: {file_path}") from exc
    payload = parse_json_object(raw, file_path.name)
    if payload.get("month") != month:
        raise CorruptionError(f"month mismatch in {file_path.name}")
    if not isinstance(payload.get("version"), int):
        raise CorruptionError(f"{file_path.name} missing integer version")
    if not isinstance(payload.get("todos"), list):
        raise CorruptionError(f"{file_path.name} invalid todos list")
    for todo in payload["todos"]:
        validate_todo_shape(todo)
        if todo["archive_month"] != month:
            raise CorruptionError(f"{file_path.name} contains todo with mismatched archive_month")
    return payload


def write_month_file(paths: StorePaths, month: str, payload: Dict[str, Any]) -> None:
    atomic_write_json(paths.month_file(month), payload)


def write_index(paths: StorePaths, index: Dict[str, Any]) -> None:
    atomic_write_json(paths.index_file, index)


def normalize_tags(tags: List[str]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for tag in tags:
        clean = tag.strip()
        if clean and clean not in seen:
            normalized.append(clean)
            seen.add(clean)
    return normalized


def recalc_stats(paths: StorePaths, index: Dict[str, Any]) -> None:
    counts = {STATUS_OPEN: 0, STATUS_DONE: 0, STATUS_CANCELED: 0, STATUS_DELETED: 0}
    for month in index["months"]:
        payload = ensure_month_file(paths, month)
        for todo in payload["todos"]:
            counts[todo["status"]] = counts.get(todo["status"], 0) + 1
    index["stats"] = {
        "open": counts[STATUS_OPEN],
        "done": counts[STATUS_DONE],
        "canceled": counts[STATUS_CANCELED],
        "deleted": counts[STATUS_DELETED],
        "updated_at": now_cn_iso(),
    }


def add_month_if_missing(index: Dict[str, Any], month: str) -> None:
    if month not in index["months"]:
        index["months"].append(month)
        index["months"].sort()


def locate_todo(paths: StorePaths, index: Dict[str, Any], todo_id: str) -> tuple[str, Dict[str, Any], int]:
    mapped_month = index["id_map"].get(todo_id)
    if mapped_month:
        payload = ensure_month_file(paths, mapped_month)
        for idx, item in enumerate(payload["todos"]):
            if item["id"] == todo_id:
                return mapped_month, payload, idx
        raise CorruptionError(f"id_map points to missing todo id: {todo_id}")
    for month in index["months"]:
        payload = ensure_month_file(paths, month)
        for idx, item in enumerate(payload["todos"]):
            if item["id"] == todo_id:
                index["id_map"][todo_id] = month
                return month, payload, idx
    raise NotFoundError(f"todo not found: {todo_id}")


def ensure_status_transition(current: str, target: str) -> None:
    if current == target:
        return
    allowed = {
        STATUS_OPEN: {STATUS_DONE, STATUS_CANCELED, STATUS_DELETED},
        STATUS_DONE: {STATUS_OPEN, STATUS_DELETED},
        STATUS_CANCELED: {STATUS_OPEN, STATUS_DELETED},
        STATUS_DELETED: {STATUS_OPEN},
    }
    if target not in allowed.get(current, set()):
        raise ValidationError(f"status transition not allowed: {current} -> {target}")


def get_filter_date(todo: Dict[str, Any], by: str) -> Optional[date]:
    if by == BY_CREATED:
        return parse_iso_datetime(todo["created_at"], "created_at").astimezone(TZ_CN).date()
    if by == BY_PLAN:
        value = todo.get("plan_date")
        return parse_day(value, "plan_date") if value else None
    if by == BY_DUE:
        value = todo.get("due_date")
        return parse_day(value, "due_date") if value else None
    raise ValidationError(f"invalid by option: {by}")


def is_overdue(todo: Dict[str, Any], today: date) -> bool:
    if todo.get("status") != STATUS_OPEN:
        return False
    due = todo.get("due_date")
    if not due:
        return False
    return parse_day(due, "due_date") < today


def format_todo_line(todo: Dict[str, Any], today: date) -> str:
    parts = [f"[{todo['status'].upper()}]", todo["id"], todo["title"]]
    parts.append(f"type={todo['type']}")
    if todo.get("plan_date"):
        parts.append(f"plan={todo['plan_date']}")
    if todo.get("due_date"):
        due_text = todo["due_date"]
        if is_overdue(todo, today):
            due_text += "(overdue)"
        parts.append(f"due={due_text}")
    if todo.get("tags"):
        parts.append("tags=" + ",".join(todo["tags"]))
    parts.append(f"month={todo['archive_month']}")
    return " | ".join(parts)


def choose_candidate_months(index: Dict[str, Any], by: str, from_day: Optional[date], to_day: Optional[date]) -> List[str]:
    if from_day is None and to_day is None:
        return list(index["months"])
    start_day = from_day if from_day else date(1970, 1, 1)
    end_day = to_day if to_day else date(2100, 12, 31)
    if start_day > end_day:
        raise ValidationError("--from cannot be greater than --to")
    if by in (BY_PLAN, BY_DUE):
        start_month = date(start_day.year, start_day.month, 1)
        end_month = date(end_day.year, end_day.month, 1)
        months: List[str] = []
        cursor = start_month
        while cursor <= end_month:
            months.append(cursor.strftime(MONTH_FMT))
            if cursor.month == 12:
                cursor = date(cursor.year + 1, 1, 1)
            else:
                cursor = date(cursor.year, cursor.month + 1, 1)
        return [m for m in months if m in index["months"]]
    return list(index["months"])


def sort_todos_for_view(todos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def key_fn(todo: Dict[str, Any]) -> tuple[int, date, int]:
        open_rank = 0 if todo["status"] == STATUS_OPEN else 1
        due_raw = todo.get("due_date")
        due_day = parse_day(due_raw, "due_date") if due_raw else date.max
        created = parse_iso_datetime(todo["created_at"], "created_at").astimezone(TZ_CN)
        return (open_rank, due_day, -int(created.timestamp()))

    return sorted(todos, key=key_fn)


def create_todo_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    title = args.title.strip()
    if not title:
        raise ValidationError("title cannot be empty")
    if args.type not in ALL_TYPES:
        raise ValidationError(f"invalid type: {args.type}")
    plan_date = args.plan
    due_date = args.due
    if plan_date:
        parse_day(plan_date, "plan_date")
    if due_date:
        parse_day(due_date, "due_date")
    if args.type == TYPE_SHORT and not plan_date:
        raise ValidationError("short todo requires --plan")

    now_iso = now_cn_iso()
    todo: Dict[str, Any] = {
        "id": make_id(),
        "title": title,
        "type": args.type,
        "status": STATUS_OPEN,
        "created_at": now_iso,
        "updated_at": now_iso,
        "plan_date": plan_date,
        "due_date": due_date,
        "tags": normalize_tags(args.tag or []),
        "note": args.note if args.note is not None else None,
        "archive_month": "",
    }
    todo["archive_month"] = compute_archive_month(todo)
    return todo


def cmd_init(args: argparse.Namespace, base_paths: StorePaths) -> int:
    if args.default and args.data_dir:
        raise ValidationError("use either --default or --data-dir, not both")

    if not args.default and not args.data_dir:
        raise ValidationError(
            "init requires one choice: --default (use skill/data) or --data-dir /absolute/path"
        )

    if args.default:
        data_dir = (base_paths.skill_root / "data").resolve()
    else:
        selected = pathlib.Path(args.data_dir)
        if not selected.is_absolute():
            raise ValidationError("--data-dir must be an absolute path")
        data_dir = selected.resolve()

    cfg = {
        "version": VERSION,
        "initialized": True,
        "data_dir": str(data_dir),
        "timezone": DEFAULT_TIMEZONE,
    }
    write_config(base_paths, cfg)
    paths = base_paths.with_data_dir(data_dir)
    ensure_index(paths)
    print("Initialized todo skill.")
    print(f"Config: {base_paths.config_file}")
    print(f"DataDir: {paths.data_dir}")
    return 0


def cmd_add(args: argparse.Namespace, paths: StorePaths) -> int:
    index = ensure_index(paths)
    todo = create_todo_from_args(args)
    month = todo["archive_month"]
    payload = ensure_month_file(paths, month)
    payload["todos"].insert(0, todo)
    write_month_file(paths, month, payload)
    add_month_if_missing(index, month)
    index["id_map"][todo["id"]] = month
    recalc_stats(paths, index)
    write_index(paths, index)
    print(f"Added: {format_todo_line(todo, now_cn().date())}")
    print(f"DataDir: {paths.data_dir}")
    return 0


def match_status(todo: Dict[str, Any], status_filter: str) -> bool:
    return status_filter == "all" or todo["status"] == status_filter


def match_date_range(todo: Dict[str, Any], by: str, from_day: Optional[date], to_day: Optional[date]) -> bool:
    if from_day is None and to_day is None:
        return True
    target = get_filter_date(todo, by)
    if target is None:
        return False
    if from_day is not None and target < from_day:
        return False
    if to_day is not None and target > to_day:
        return False
    return True


def match_due_state(todo: Dict[str, Any], due_state: str, today: date) -> bool:
    if due_state == DUE_ALL:
        return True
    overdue = is_overdue(todo, today)
    if due_state == DUE_OVERDUE:
        return overdue
    if due_state == DUE_NOT_OVERDUE:
        return not overdue
    raise ValidationError(f"invalid due-state: {due_state}")


def cmd_list(args: argparse.Namespace, paths: StorePaths) -> int:
    if args.status != "all" and args.status not in ALL_STATUS:
        raise ValidationError(f"invalid --status value: {args.status}")
    if args.by not in ALL_BY:
        raise ValidationError(f"invalid --by value: {args.by}")
    if args.due_state not in ALL_DUE_STATE:
        raise ValidationError(f"invalid --due-state value: {args.due_state}")

    from_day = parse_day(args.from_date, "--from") if args.from_date else None
    to_day = parse_day(args.to_date, "--to") if args.to_date else None
    if from_day and to_day and from_day > to_day:
        raise ValidationError("--from cannot be greater than --to")

    index = ensure_index(paths)
    candidate_months = choose_candidate_months(index, args.by, from_day, to_day)
    today = now_cn().date()
    todos: List[Dict[str, Any]] = []
    for month in candidate_months:
        payload = ensure_month_file(paths, month)
        for todo in payload["todos"]:
            if not match_status(todo, args.status):
                continue
            if not match_date_range(todo, args.by, from_day, to_day):
                continue
            if not match_due_state(todo, args.due_state, today):
                continue
            todos.append(todo)
    todos = sort_todos_for_view(todos)
    if args.json:
        print(json.dumps({"data_dir": str(paths.data_dir), "count": len(todos), "todos": todos}, ensure_ascii=False, indent=2))
        return 0
    if not todos:
        print("No todos matched.")
        print(f"DataDir: {paths.data_dir}")
        return 0
    for todo in todos:
        print(format_todo_line(todo, today))
    print(f"DataDir: {paths.data_dir}")
    return 0


def cmd_show(args: argparse.Namespace, paths: StorePaths) -> int:
    index = ensure_index(paths)
    month, payload, idx = locate_todo(paths, index, args.id)
    todo = payload["todos"][idx]
    if args.json:
        print(json.dumps({"month": month, "todo": todo, "data_dir": str(paths.data_dir)}, ensure_ascii=False, indent=2))
    else:
        print(format_todo_line(todo, now_cn().date()))
        print(f"DataDir: {paths.data_dir}")
    return 0


def persist_todo_update(
    paths: StorePaths,
    index: Dict[str, Any],
    current_month: str,
    payload: Dict[str, Any],
    idx: int,
    todo: Dict[str, Any],
) -> None:
    target_month = compute_archive_month(todo)
    todo["archive_month"] = target_month
    if target_month == current_month:
        payload["todos"][idx] = todo
        write_month_file(paths, current_month, payload)
    else:
        payload["todos"].pop(idx)
        write_month_file(paths, current_month, payload)
        target_payload = ensure_month_file(paths, target_month)
        target_payload["todos"].insert(0, todo)
        write_month_file(paths, target_month, target_payload)
        add_month_if_missing(index, target_month)
    index["id_map"][todo["id"]] = target_month
    recalc_stats(paths, index)
    write_index(paths, index)


def set_status(paths: StorePaths, todo_id: str, target_status: str, action_label: str) -> int:
    index = ensure_index(paths)
    month, payload, idx = locate_todo(paths, index, todo_id)
    todo = payload["todos"][idx]
    ensure_status_transition(todo["status"], target_status)
    todo["status"] = target_status
    todo["updated_at"] = now_cn_iso()
    persist_todo_update(paths, index, month, payload, idx, todo)
    print(f"{action_label}: {format_todo_line(todo, now_cn().date())}")
    print(f"DataDir: {paths.data_dir}")
    return 0


def cmd_done(args: argparse.Namespace, paths: StorePaths) -> int:
    return set_status(paths, args.id, STATUS_DONE, "Done")


def cmd_reopen(args: argparse.Namespace, paths: StorePaths) -> int:
    return set_status(paths, args.id, STATUS_OPEN, "Reopened")


def cmd_cancel(args: argparse.Namespace, paths: StorePaths) -> int:
    return set_status(paths, args.id, STATUS_CANCELED, "Canceled")


def cmd_rm(args: argparse.Namespace, paths: StorePaths) -> int:
    return set_status(paths, args.id, STATUS_DELETED, "SoftDeleted")


def cmd_update(args: argparse.Namespace, paths: StorePaths) -> int:
    index = ensure_index(paths)
    month, payload, idx = locate_todo(paths, index, args.id)
    todo = dict(payload["todos"][idx])
    changed = False
    if args.title is not None:
        title = args.title.strip()
        if not title:
            raise ValidationError("title cannot be empty")
        todo["title"] = title
        changed = True
    if args.type is not None:
        todo["type"] = args.type
        changed = True
    if args.plan is not None:
        parse_day(args.plan, "plan_date")
        todo["plan_date"] = args.plan
        changed = True
    if args.clear_plan:
        todo["plan_date"] = None
        changed = True
    if args.due is not None:
        parse_day(args.due, "due_date")
        todo["due_date"] = args.due
        changed = True
    if args.clear_due:
        todo["due_date"] = None
        changed = True
    if args.tag is not None:
        todo["tags"] = normalize_tags(args.tag)
        changed = True
    if args.note is not None:
        todo["note"] = args.note
        changed = True
    if args.clear_note:
        todo["note"] = None
        changed = True
    if todo["type"] == TYPE_SHORT and not todo.get("plan_date"):
        raise ValidationError("short todo requires plan_date")
    if not changed:
        raise ValidationError("no updates provided")
    todo["updated_at"] = now_cn_iso()
    persist_todo_update(paths, index, month, payload, idx, todo)
    print(f"Updated: {format_todo_line(todo, now_cn().date())}")
    print(f"DataDir: {paths.data_dir}")
    return 0


def cmd_overdue(args: argparse.Namespace, paths: StorePaths) -> int:
    args.status = STATUS_OPEN
    args.by = args.by or BY_DUE
    args.due_state = DUE_OVERDUE
    return cmd_list(args, paths)


def build_parser() -> StrictArgumentParser:
    parser = StrictArgumentParser(prog="todo.py", add_help=True)
    sub = parser.add_subparsers(dest="cmd", required=True)

    init_p = sub.add_parser("init", help="initialize storage path")
    init_p.add_argument("--default", action="store_true", help="use <skill>/data as storage")
    init_p.add_argument("--data-dir", default=None, help="absolute storage directory path")
    init_p.set_defaults(handler=cmd_init)

    add_p = sub.add_parser("add", help="add todo")
    add_p.add_argument("--type", required=True, choices=sorted(ALL_TYPES))
    add_p.add_argument("--title", required=True)
    add_p.add_argument("--plan", default=None, help="YYYY-MM-DD")
    add_p.add_argument("--due", default=None, help="YYYY-MM-DD")
    add_p.add_argument("--tag", action="append", default=[])
    add_p.add_argument("--note", default=None)
    add_p.set_defaults(handler=cmd_add)

    list_p = sub.add_parser("list", help="list todos")
    list_p.add_argument("--status", default=STATUS_OPEN, help="open|done|canceled|deleted|all")
    list_p.add_argument("--by", default=BY_CREATED, help="created|plan|due")
    list_p.add_argument("--from", dest="from_date", default=None, help="YYYY-MM-DD")
    list_p.add_argument("--to", dest="to_date", default=None, help="YYYY-MM-DD")
    list_p.add_argument("--due-state", default=DUE_ALL, help="overdue|not-overdue|all")
    list_p.add_argument("--json", action="store_true")
    list_p.set_defaults(handler=cmd_list)

    show_p = sub.add_parser("show", help="show single todo")
    show_p.add_argument("--id", required=True)
    show_p.add_argument("--json", action="store_true")
    show_p.set_defaults(handler=cmd_show)

    done_p = sub.add_parser("done", help="mark done")
    done_p.add_argument("--id", required=True)
    done_p.set_defaults(handler=cmd_done)

    reopen_p = sub.add_parser("reopen", help="reopen todo")
    reopen_p.add_argument("--id", required=True)
    reopen_p.set_defaults(handler=cmd_reopen)

    cancel_p = sub.add_parser("cancel", help="cancel todo")
    cancel_p.add_argument("--id", required=True)
    cancel_p.set_defaults(handler=cmd_cancel)

    rm_p = sub.add_parser("rm", help="soft delete todo")
    rm_p.add_argument("--id", required=True)
    rm_p.set_defaults(handler=cmd_rm)

    update_p = sub.add_parser("update", help="update todo")
    update_p.add_argument("--id", required=True)
    update_p.add_argument("--title", default=None)
    update_p.add_argument("--type", default=None, choices=sorted(ALL_TYPES))
    update_p.add_argument("--plan", default=None, help="YYYY-MM-DD")
    update_p.add_argument("--clear-plan", action="store_true")
    update_p.add_argument("--due", default=None, help="YYYY-MM-DD")
    update_p.add_argument("--clear-due", action="store_true")
    update_p.add_argument("--tag", action="append")
    update_p.add_argument("--note", default=None)
    update_p.add_argument("--clear-note", action="store_true")
    update_p.set_defaults(handler=cmd_update)

    overdue_p = sub.add_parser("overdue", help="list overdue open todos")
    overdue_p.add_argument("--by", default=BY_DUE, help="created|plan|due")
    overdue_p.add_argument("--from", dest="from_date", default=None, help="YYYY-MM-DD")
    overdue_p.add_argument("--to", dest="to_date", default=None, help="YYYY-MM-DD")
    overdue_p.add_argument("--json", action="store_true")
    overdue_p.set_defaults(handler=cmd_overdue)
    return parser


def run() -> int:
    parser = build_parser()
    args = parser.parse_args()
    base_paths = StorePaths.from_script()
    try:
        if args.cmd == "init":
            return int(args.handler(args, base_paths))

        cfg = load_config(base_paths)
        ensure_initialized(base_paths, cfg)
        paths = base_paths.with_data_dir(resolve_configured_data_dir(base_paths, cfg))
        return int(args.handler(args, paths))
    except TodoError as exc:
        print(f"{exc.prefix}: {exc.message}", file=os.sys.stderr)
        if exc.exit_code in (EXIT_STORAGE, EXIT_CORRUPTION):
            print(f"Config: {base_paths.config_file}", file=os.sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(run())

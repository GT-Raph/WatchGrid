"""WatchGrid monitor-status patch v2: resilient to formatting differences.

Run beside manage.py: python apply_fix.py --check, then python apply_fix.py.
All source files are parsed/validated and written only after every check passes.
Original files are backed up beneath .watchgrid_backup/. No database data is edited.
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
CHECK_CALL = "transaction.on_commit(_request_initial_checks)"
HELPER_MARKER = "def _request_initial_checks()"

HELPER = '''\ndef _request_initial_checks() -> None:\n    """Prompt a first scheduled check after a monitor transaction commits.\n\n    The normal scheduler still handles routine checks. Network/broker errors\n    are logged; the pending check remains available to the next scheduler tick.\n    """\n    if not getattr(settings, "MONITOR_IMMEDIATE_CHECKS", True):\n        return\n    from apps.checks.tasks import dispatch_due_monitors\n\n    try:\n        dispatch_due_monitors.delay()\n    except Exception:\n        logging.getLogger("watchgrid.monitoring").exception(\n            "Could not dispatch immediate monitor checks; scheduler will retry"\n        )\n\n'''


def module_ast(source: str, path: str) -> ast.Module:
    try:
        return ast.parse(source, filename=path)
    except SyntaxError as exc:
        raise ValueError(f"Cannot parse {path}: {exc}") from exc


def functions(source: str, path: str):
    return {item.name: item for item in module_ast(source, path).body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}


def insert_before_top_level_return(source: str, function_name: str, statement: str) -> str:
    path = "apps/monitors/services.py"
    tree = functions(source, path)
    func = tree.get(function_name)
    if func is None:
        raise ValueError(f"Expected a {function_name} function in {path}.")
    lines = source.splitlines(keepends=True)
    # Scan *direct* statements only to avoid inserting into a nested conditional
    # or a nested transaction block before the monitor is saved.
    returns = [stmt for stmt in func.body
               if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Name)
               and stmt.value.id == "monitor"]
    if not returns:
        raise ValueError(f"Could not identify the final `return monitor` in {function_name}.")
    final_return = returns[-1]
    start = final_return.lineno - 1
    indentation = lines[start][:len(lines[start]) - len(lines[start].lstrip())]
    # Idempotency: only inspect code between this function's start and final return.
    before_return = "".join(lines[func.lineno - 1:start])
    if statement in before_return:
        return source
    lines.insert(start, indentation + statement + "\n")
    updated = "".join(lines)
    module_ast(updated, path)
    return updated


def update_services(source: str) -> str:
    path = "apps/monitors/services.py"
    tree = module_ast(source, path)
    if "_request_initial_checks" not in functions(source, path):
        # Add a self-contained helper before the first monitor service function,
        # instead of matching an exact source string that may differ locally.
        funcs = functions(source, path)
        first = funcs.get("create_monitor")
        if first is None:
            raise ValueError("create_monitor was not found in apps/monitors/services.py")
        lines = source.splitlines(keepends=True)
        lines.insert(first.lineno - 1, HELPER)
        source = "".join(lines)
    if not any(isinstance(node, ast.Import) and any(alias.name == "logging" for alias in node.names)
               for node in tree.body):
        lines = source.splitlines(keepends=True)
        first = next((i for i, line in enumerate(lines) if line.startswith("from __future__ import")), None)
        if first is None:
            # No future import: insert after any encoding comment or docstring using AST.
            doc_end = tree.body[0].end_lineno if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant) and isinstance(tree.body[0].value.value, str) else 0
            lines.insert(doc_end, "\nimport logging\n")
        else:
            lines.insert(first + 1, "\nimport logging\n")
        source = "".join(lines)
    if not "from django.conf import settings" in source or not "transaction" in source:
        raise ValueError("Expected Django settings and transaction imports in services.py")
    source = insert_before_top_level_return(source, "create_monitor", CHECK_CALL)
    source = insert_before_top_level_return(
        source, "update_monitor",
        'if monitor.is_active and monitor.status == "unknown":\n        ' + CHECK_CALL,
    )
    source = insert_before_top_level_return(source, "resume_monitor", CHECK_CALL)
    module_ast(source, path)
    return source


def update_template_filter(source: str) -> str:
    path = "common/utilities/templatetags.py"
    funcs = functions(source, path)
    fn = funcs.get("label")
    if fn is None:
        raise ValueError(f"Missing label() in {path}")
    body = source.splitlines(keepends=True)
    contents = "".join(body[fn.lineno - 1:fn.end_lineno])
    if 'return "Checking…"' in contents:
        return source
    # Don't replace the existing logic, just short-circuit the monitor's special
    # unknown status. Other labels still behave as before.
    first = fn.body[0]
    insertion = "    if str(value).strip().lower() == \"unknown\":\n        return \"Checking…\"\n"
    body.insert(first.lineno - 1, insertion)
    updated = "".join(body)
    module_ast(updated, path)
    return updated


def update_views(source: str) -> str:
    path = "apps/monitors/views.py"
    if "Checking…" in source and "status_choices" in source:
        return source
    import re
    match = re.search(r"(?m)^(?P<indent>\s*)status_choices\s*=\s*Monitor\.Status\.choices\s*,?\s*$", source)
    if match is None:
        raise ValueError("Cannot find status_choices=Monitor.Status.choices in monitors/views.py")
    ind = match.group("indent")
    replacement = (f"{ind}status_choices=[\n"
                   f"{ind}    (code, \"Checking…\" if code == Monitor.Status.UNKNOWN else label)\n"
                   f"{ind}    for code, label in Monitor.Status.choices\n"
                   f"{ind}],")
    updated = source[:match.start()] + replacement + source[match.end():]
    module_ast(updated, path)
    return updated


def update_test_settings(source: str) -> str:
    if "MONITOR_IMMEDIATE_CHECKS" in source:
        return source
    # No dependency on the exact position or wording of Celery settings.
    return source.rstrip() + "\n\n# The test suite controls scheduler dispatch explicitly.\nMONITOR_IMMEDIATE_CHECKS = False\n"


UPDATES = {
    "apps/monitors/services.py": update_services,
    "common/utilities/templatetags.py": update_template_filter,
    "apps/monitors/views.py": update_views,
    "config/settings/test.py": update_test_settings,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate without modifying files")
    args = parser.parse_args()
    if not (ROOT / "manage.py").is_file():
        print("Place apply_fix.py next to manage.py in the WatchGrid project folder.", file=sys.stderr)
        return 1
    prepared = {}
    for rel, change in UPDATES.items():
        file_path = ROOT / rel
        if not file_path.is_file():
            print(f"Missing required file: {rel}. Nothing changed.", file=sys.stderr)
            return 1
        original = file_path.read_text(encoding="utf-8")
        try:
            updated = change(original)
            if file_path.suffix == ".py":
                module_ast(updated, rel)
        except (ValueError, SyntaxError) as exc:
            print(f"Fix could not be validated: {exc}\nNothing changed. Please share {rel} so I can adapt the patch.", file=sys.stderr)
            return 1
        prepared[rel] = (original, updated)
    for rel, (original, updated) in prepared.items():
        if original == updated:
            print(f"UNCHANGED  {rel}")
        elif args.check:
            print(f"READY      {rel}")
        else:
            backup = ROOT / ".watchgrid_backup" / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                backup.write_text(original, encoding="utf-8")
            (ROOT / rel).write_text(updated, encoding="utf-8")
            print(f"UPDATED    {rel}")
    if args.check:
        print("Validation complete; no files changed. Run python apply_fix.py to apply.")
    else:
        print("Patch applied. Run python manage.py check, then start the server and scheduler.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

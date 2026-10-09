# WatchGrid: “Unknown” status fix — version 2

This package replaces the earlier patch, which could stop with `Unexpected source in apps/monitors/services.py: required text not found` because it depended on an exact block of text.

## What is fixed

- Monitor status `unknown` displays as **Checking…** in dashboard text and badges.
- Monitor status dropdown also says **Checking…**.
- Creating a monitor, resuming a monitor, or changing its target triggers an immediate *scheduled* check after transaction commit.
- Actual operational/degraded/down/paused status still depends on real monitoring checks. Unknown is not falsely marked operational.
- Automated-test settings keep immediate dispatch disabled by default.

## Windows installation

1. Copy **apply_fix.py** and **start_watchgrid.ps1** beside `manage.py` in your local WatchGrid folder. Replace the previous `apply_fix.py`.
2. In PowerShell, run:

   ```powershell
   python apply_fix.py --check
   ```

   This is a dry run. If it reports READY, proceed.

3. Apply and validate:

   ```powershell
   python apply_fix.py
   .\.venv\Scripts\python.exe manage.py check
   ```

4. Start Django and its local scheduler together:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\start_watchgrid.ps1
   ```

   Alternatively start these commands in separate terminal windows:

   ```powershell
   .\.venv\Scripts\python.exe manage.py runserver
   .\.venv\Scripts\python.exe manage.py run_scheduler
   ```

5. Refresh the dashboard, and check the monitor's **Last checked** timestamp. The new site's status should advance from **Checking…** to the actual observed state as soon as a check completes.

## Safety and recovery

- Dry-run `--check` never changes any files. If a source file is structurally different, the script stops without modifying files and prints which file to share for further adaptation.
- Every changed source file is backed up once under `.watchgrid_backup/`.
- Run `python -m pytest` inside your environment before pushing changes to production.
- Do not manually edit or delete `db.sqlite3`; existing monitor data/history is preserved.
- This patch does **not** enable monitoring by simply renaming a badge. The scheduler must run. If the status stays **Checking…**, inspect the scheduler output, check history, network access and the worker/broker setup.
- Hosted WatchGrid needs a running Celery worker and Celery beat; `start_watchgrid.ps1` is only for local development.

This package was validated for Python syntax and idempotency against representative code. The user's locally modified repository and complete Django test suite cannot be executed in this environment.

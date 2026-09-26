"""Bounded, read-only size sampling outside the structured history budget."""
from datetime import datetime, timezone
import os
from pathlib import Path
import time


def sample_existing_files(root, max_entries=10000, max_seconds=.15):
    root = Path(root)
    totals = {'text_logs': 0, 'screenshots': 0, 'config_backups': 0, 'other_log_files': 0}
    roots = [('log', 'log'), ('screenshot', 'screenshots'), ('screenshots', 'screenshots'),
             ('config/backup', 'config_backups'), ('config/backups', 'config_backups')]
    seen, count, complete = set(), 0, True
    deadline = time.monotonic() + max_seconds
    stack = [(root / name, category) for name, category in roots]
    while stack:
        directory, category = stack.pop()
        if time.monotonic() > deadline or count >= max_entries:
            complete = False
            break
        try:
            if directory.is_symlink() or not directory.exists():
                continue
            if getattr(directory.stat(follow_symlinks=False), 'st_file_attributes', 0) & 0x400:
                complete = False
                continue
            resolved = str(directory.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            with os.scandir(directory) as entries:
                for entry in entries:
                    count += 1
                    if count > max_entries or time.monotonic() > deadline:
                        complete = False
                        break
                    if entry.is_symlink() or getattr(entry.stat(follow_symlinks=False), 'st_file_attributes', 0) & 0x400:
                        complete = False
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append((Path(entry.path), category))
                    elif entry.is_file(follow_symlinks=False):
                        key = category
                        if category == 'log':
                            suffix = Path(entry.name).suffix.lower()
                            key = ('screenshots' if suffix in ('.png', '.jpg', '.jpeg', '.webp', '.bmp')
                                   else 'text_logs' if suffix in ('.txt', '.log', '.jsonl') else 'other_log_files')
                        totals[key] += entry.stat(follow_symlinks=False).st_size
            if not complete:
                break
        except OSError:
            complete = False
    return {'bytes': totals, 'complete': complete, 'scanned_entries': count,
            'observed_at': datetime.now(timezone.utc).isoformat(),
            'scope': [name for name, _ in roots], 'included_in_budget': False,
            'note': '仅统计当前后端的已知日志、截图和备份目录；不包含其他安装或自定义路径。' +
                    ('' if complete else '扫描达到限制或有文件不可读，显示已扫描部分。')}

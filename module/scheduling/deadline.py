"""Optional wall-clock task cutoff, shared by configuration and dispatch."""
from datetime import datetime


def deadline_timestamp(value):
    """Keep legacy naive values in local time and honor explicit UTC offsets."""
    if value is None or value == '':
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        if value.endswith('Z'):
            value = value[:-1] + '+00:00'  # Python 3.10 compatibility
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise ValueError('活动截止时间必须是有效的日期和时间')
    return value.timestamp()


def task_deadline(task_config):
    data = (task_config.model_dump(mode='json')
            if hasattr(task_config, 'model_dump') else task_config)
    return deadline_timestamp((data or {}).get('scheduler', {}).get('real_deadline'))


def is_expired(deadline, now):
    return deadline is not None and now >= deadline

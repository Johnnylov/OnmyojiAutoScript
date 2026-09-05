"""神秘商店独立运行时间，不受外部调度器重置 next_run 影响。"""

from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path

from module.config.utils import read_file, write_file


class MysteryShopSchedule:
    VERSION = 1
    OPEN_WEEKDAYS = (2, 5)

    def __init__(self, config_name: str, root: Path | None = None):
        if not isinstance(config_name, str) or not config_name.strip():
            raise ValueError('MysteryShop schedule requires a non-empty config name')
        self.config_name = config_name
        self.root = (Path(root) if root is not None else
                     Path(__file__).resolve().parents[2] / 'config' / '.runtime' / 'mystery_shop')
        # 配置名称不参与目录拼接，不同账号配置分别保存且不产生非法路径。
        key = sha256(config_name.encode('utf-8')).hexdigest()
        self.path = self.root / f'{key}.json'

    @classmethod
    def _validate_target(cls, target: datetime) -> datetime:
        if not isinstance(target, datetime) or target.tzinfo is not None:
            raise ValueError('MysteryShop next run must be a local datetime without a timezone')
        if target.weekday() not in cls.OPEN_WEEKDAYS:
            raise ValueError('MysteryShop next run must be on Wednesday or Saturday')
        return target.replace(microsecond=0)

    def read_next_run(self) -> datetime | None:
        """缺少状态才允许首次运行；已有状态损坏时阻止重复购买。"""
        try:
            self.path.stat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise OSError(f'Cannot inspect MysteryShop schedule state: {self.path}') from exc

        try:
            data = read_file(str(self.path))
        except ValueError as exc:
            raise ValueError(f'Corrupt MysteryShop schedule state: {self.path}') from exc
        except OSError as exc:
            raise OSError(f'Cannot read MysteryShop schedule state: {self.path}') from exc

        if (not isinstance(data, dict)
                or type(data.get('version')) is not int
                or data.get('version') != self.VERSION
                or data.get('config_name') != self.config_name
                or not isinstance(data.get('next_run'), str)):
            raise ValueError(f'Invalid MysteryShop schedule state: {self.path}')
        try:
            target = datetime.fromisoformat(data['next_run'])
            return self._validate_target(target)
        except ValueError as exc:
            raise ValueError(f'Invalid MysteryShop next run in state: {self.path}') from exc

    def write_next_run(self, target: datetime) -> None:
        """完整购买流程成功后，由调用方保存下一开放日；写入失败不假装成功。"""
        target = self._validate_target(target)
        data = {
            'version': self.VERSION,
            'config_name': self.config_name,
            'next_run': target.isoformat(timespec='seconds'),
        }
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            # 与普通配置复用加锁、原子替换机制，但写入独立状态文件。
            write_file(str(self.path), data)
        except (OSError, ValueError) as exc:
            raise OSError(f'Cannot persist MysteryShop schedule state: {self.path}') from exc

    def resolve_next_run(self, now: datetime) -> datetime | None:
        """按已保存的时刻恢复调度，过期状态顺延到今天或下一个开放日。"""
        if not isinstance(now, datetime) or now.tzinfo is not None:
            raise ValueError('MysteryShop current time must be a local datetime without a timezone')
        target = self.read_next_run()
        if target is None or target > now:
            return target

        # 过期状态保留原来的执行时刻；开放日当天未到点仍然等待，到点后才待运行。
        target_date = now.date()
        while target_date.weekday() not in self.OPEN_WEEKDAYS:
            target_date += timedelta(days=1)
        return datetime.combine(target_date, target.time())

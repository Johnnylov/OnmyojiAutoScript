"""按账号、日期和好友记录商店分享，重启或购买失败不重复发送。"""

from datetime import date
from hashlib import sha256
from pathlib import Path

from filelock import FileLock, Timeout

from module.config.utils import read_file, write_file


class MysteryShopShareState:
    VERSION = 1

    def __init__(self, config_name: str, root: Path | None = None):
        if not isinstance(config_name, str) or not config_name.strip():
            raise ValueError('MysteryShop shares require a non-empty config name')
        self.config_name = config_name
        self.root = (Path(root) if root is not None else
                     Path(__file__).resolve().parents[2] / 'config' / '.runtime' / 'mystery_shop_shares')
        key = sha256(config_name.encode('utf-8')).hexdigest()
        self.path = self.root / f'{key}.json'

    @staticmethod
    def _friend_key(friend: str) -> str:
        if not isinstance(friend, str):
            raise ValueError('MysteryShop share friend must be a name')
        # 与邀请界面的精确姓名匹配使用相同的空白归一化，不另存好友明文。
        friend = friend.replace(' ', '').replace('　', '').strip()
        if not friend:
            raise ValueError('MysteryShop share friend cannot be empty')
        return sha256(friend.encode('utf-8')).hexdigest()

    def _read(self, day: date) -> dict:
        empty = {'version': self.VERSION, 'config_name': self.config_name,
                 'day': day.isoformat(), 'friends': {}}
        try:
            self.path.stat()
        except FileNotFoundError:
            return empty
        data = read_file(str(self.path))
        if (not isinstance(data, dict)
                or type(data.get('version')) is not int or data['version'] != self.VERSION
                or data.get('config_name') != self.config_name
                or not isinstance(data.get('day'), str)
                or not isinstance(data.get('friends'), dict)):
            raise ValueError(f'Invalid MysteryShop daily share state: {self.path}')
        if date.fromisoformat(data['day']).isoformat() != data['day']:
            raise ValueError(f'Invalid MysteryShop share date: {self.path}')
        for key, status in data['friends'].items():
            if (not isinstance(key, str) or len(key) != 64
                    or any(char not in '0123456789abcdef' for char in key)
                    or status not in ('pending', 'shared')):
                raise ValueError(f'Invalid MysteryShop share entry: {self.path}')
        return data if data['day'] == day.isoformat() else empty

    def _change(self, friend: str, day: date, action: str) -> bool:
        if type(day) is not date:
            raise ValueError('MysteryShop share day must be a local calendar date')
        key = self._friend_key(friend)
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            # guard 包含整个读改写，防止同一账号的两个进程同时发送。
            # JSON 内部继续使用通用配置的加锁和原子替换，二者锁文件不同。
            with FileLock(f'{self.path}.guard.lock', timeout=5):
                data = self._read(day)
                status = data['friends'].get(key)
                if action == 'reserve':
                    if status is not None:
                        return False
                    data['friends'][key] = 'pending'
                elif action == 'complete':
                    if status is None:
                        raise ValueError('MysteryShop share has no pending reservation')
                    data['friends'][key] = 'shared'
                elif action == 'release':
                    if status != 'pending':
                        return False
                    del data['friends'][key]
                else:
                    raise ValueError('Unknown MysteryShop share state action')
                write_file(str(self.path), data)
                return True
        except Timeout as exc:
            raise OSError(f'MysteryShop daily share state is busy: {self.path}') from exc

    def reserve(self, friend: str, day: date) -> bool:
        """先持久化发送占位；崩溃后结果不明的请求当天也不自动重发。"""
        return self._change(friend, day, 'reserve')

    def complete(self, friend: str, day: date) -> None:
        self._change(friend, day, 'complete')

    def release(self, friend: str, day: date) -> None:
        """仅明确未选中/未发送时允许下次重试，不撤销成功记录。"""
        self._change(friend, day, 'release')

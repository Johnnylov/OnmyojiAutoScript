"""商城主街识别：背景屋檐会随场景变化，不能仅依赖一块背景纹理。"""

from tasks.GameUi.assets import GameUiAssets
from tasks.RichMan.assets import RichManAssets


def is_mall_page(task) -> bool:
    """使用当前截图，不点击；保留旧场景，并以多个固定底栏图标兜底。"""
    if getattr(task.device.image, 'shape', None) != (720, 1280, 3):
        return False
    # 推荐商品页必须先退出，不能将其背后的主街当成已到达。
    if task.appear(GameUiAssets.I_CHECK_MALL_RECOMMEND):
        return False
    if task.appear(GameUiAssets.I_CHECK_MALL):
        return True
    # 不放宽屋檐的阈值或全屏搜索；至少两个不同商铺图标高分命中。
    return sum(task.appear(marker, threshold=0.9) for marker in (
        RichManAssets.I_MALL_CONSIGNMENT,
        RichManAssets.I_MALL_SCCALES,
        RichManAssets.I_MALL_SUNDRY,
    )) >= 2

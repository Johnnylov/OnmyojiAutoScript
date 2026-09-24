"""月映千灯入口及月华流光两级页面。"""
from tasks.Component.RightActivity.assets import RightActivityAssets
from tasks.GameUi.page import Page, page_main
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.Moonlight.assets import MoonlightAssets


def enter_moonlight(task) -> bool:
    """仅在庭院查找入口；切换栏目不表示已进入活动。"""
    if not task.match_page_once(page_main):
        return False
    if task.appear(MoonlightAssets.I_MOON_ENTRY):
        return task.appear_then_click(MoonlightAssets.I_MOON_ENTRY, interval=0.8)
    task.appear_then_click(RightActivityAssets.I_TOGGLE_BUTTON, interval=2)
    return False


page_moon_lobby = Page(MoonlightAssets.I_MOON_LOBBY, priority=75)
page_moon_battle = Page(MoonlightAssets.I_MOON_BATTLE, priority=75)
page_main.connect(page_moon_lobby, enter_moonlight, key='main->moon_lobby')
page_moon_lobby.connect(page_main, GlobalGameAssets.I_UI_BACK_YELLOW, key='moon_lobby->main')
page_moon_lobby.connect(page_moon_battle, MoonlightAssets.I_MOON_ENTER_BATTLE,
                        key='moon_lobby->moon_battle')
page_moon_battle.connect(page_moon_lobby, GlobalGameAssets.I_UI_BACK_YELLOW,
                         key='moon_battle->moon_lobby')

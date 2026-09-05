"""Offline MysteryShop scrolling regressions; never imports Device or game config.

Run from the repository root:
    toolkit/python.exe -m unittest discover -s dev_tools -p test_mystery_shop_scroll.py

For local error-image replay, set MYSTERY_SHOP_REPLAY_IMAGES to PNG paths
separated by the platform path separator. Account screenshots are never copied.
"""

import ast
import os
import operator
from datetime import datetime, timedelta, time
from functools import cached_property
from hashlib import sha256
import json
from pathlib import Path
from types import MethodType, ModuleType, SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np
from filelock import FileLock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tasks/MysteryShop/script_task.py"
FLAGS = ("mystery_amulet", "black_daruma_scrap", "shop_kaiko_3", "shop_kaiko_4")
ITEMS = ("I_MS_BLUE", "I_MS_BLACK", "I_MS_TAIKO_3", "I_MS_TAIKO_4")
PRICES = dict(zip(ITEMS, (85, 60, 45, 80)))


def class_from_file(path, name, namespace, bases=None):
    """Execute production class bodies without import-time OCR/ADB side effects."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    node = next(node for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == name)
    if bases is not None:
        node.bases = [ast.Name(id=base, ctx=ast.Load()) for base in bases]
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__",
                             names=[ast.alias(name="annotations")], level=0), node],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[name]


def function_from_file(path, name, namespace, class_name=None):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    body = tree.body
    if class_name is not None:
        body = next(node.body for node in body
                    if isinstance(node, ast.ClassDef) and node.name == class_name)
    node = next(node for node in body
                if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__",
                             names=[ast.alias(name="annotations")], level=0), node],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[name]


class Rule:
    """Asset metadata only; matching and gestures are provided by ShopWorld."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.name = kwargs.get("name", Path(kwargs.get("file", "rule")).stem)


class MemorySchedule:
    def __init__(self, next_run=None):
        self.next_run = next_run
        self.read_next_run = Mock(side_effect=lambda: self.next_run)
        self.resolve_next_run = Mock(side_effect=lambda now: self.read_next_run())
        self.write_next_run = Mock(side_effect=self._write)

    def _write(self, target):
        self.next_run = target


class ShopWorld:
    """Deterministic list, inventory and currency behind the real task methods."""

    def __init__(self, pages, start_page=0):
        self.now = 1.0
        self.current_date = datetime(2026, 9, 5, 12)
        self.pages = [dict(page) for page in pages]
        self.page = start_page
        self.money = 1000
        self.purchases = []
        self.gestures = []
        self.failed_gestures = []
        self.operations = []
        self.reject_gestures = False
        self.reject_next_down = 0
        self.unstable = False
        self.shop_visible = True
        self.popup = None
        self.template_cache = {}
        self.screenshot_count = 0
        self.frames = [
            np.random.default_rng(index).integers(
                0, 256, size=(720, 1280, 3), dtype=np.uint8
            )
            for index in range(len(pages))
        ]
        namespace = {
            "np": np,
            "cv2": cv2,
            "logger": Mock(),
            "random": SimpleNamespace(randint=lambda low, high: (low + high) // 2),
            "datetime": self.clock_datetime(),
            "timedelta": timedelta,
            "time": time,
            "Path": Path,
            "cached_property": cached_property,
            "sleep": self.sleep,
            "RuleImage": Rule,
            "RuleSwipe": Rule,
            "RuleOcr": Rule,
            "RuleClick": Rule,
            "RuleLongClick": Rule,
            "RuleList": Rule,
        }
        exception_path = ROOT / "module/exception.py"
        exec(compile(exception_path.read_text(encoding="utf-8"),
                     str(exception_path), "exec"), namespace)
        timer_namespace = {"time": SimpleNamespace(time=lambda: self.now)}
        namespace["Timer"] = class_from_file(
            ROOT / "module/base/timer.py", "Timer", timer_namespace
        )
        namespace["RuleImage"] = class_from_file(
            ROOT / "module/atom/image.py", "RuleImage", namespace
        )
        namespace["get_image_client"] = lambda: SimpleNamespace(
            match_dynamic_template=self.match_dynamic_template,
            match_rule=self.match_rule,
        )
        namespace["RuleAnimate"] = class_from_file(
            ROOT / "module/atom/animate.py", "RuleAnimate", namespace
        )
        namespace["_Assets"] = class_from_file(
            ROOT / "tasks/MysteryShop/assets.py", "MysteryShopAssets", namespace
        )
        namespace["_MallAssets"] = class_from_file(
            ROOT / "tasks/RichMan/assets.py", "RichManAssets", namespace
        )
        namespace["_BuyAssets"] = class_from_file(
            ROOT / "tasks/Component/Buy/assets.py", "BuyAssets", namespace
        )
        namespace["_GlobalAssets"] = class_from_file(
            ROOT / "tasks/GlobalGame/assets.py", "GlobalGameAssets", namespace
        )
        namespace["_Purchasing"] = class_from_file(
            ROOT / "tasks/RichMan/mall/friendship_points.py",
            "FriendshipPoints", namespace, bases=[]
        )
        task_class = class_from_file(
            SOURCE, "ScriptTask", namespace,
            bases=["_Purchasing", "_Assets", "_MallAssets", "_BuyAssets", "_GlobalAssets"]
        )
        self.namespace = namespace
        self.task = task_class()
        self.task.device = SimpleNamespace(
            image=self.frames[self.page].copy(), image_frame_id=None,
            swipe_adb=Mock(side_effect=self.swipe_adb),
            click_record_clear=Mock(side_effect=lambda: self.operations.append(("clear",))),
        )
        self.task.screenshot = Mock(side_effect=self.screenshot)
        self.task.swipe = Mock(side_effect=AssertionError("Shop must use KekkaiUtilize's direct ADB path"))
        self.task.appear = Mock(side_effect=self.appear)
        self.task._special_check_remain = Mock(side_effect=self.remain)
        self.task.buy_one = Mock(side_effect=self.purchase)
        self.task.O_MALL_RESOURCE_5 = SimpleNamespace(ocr=lambda _: self.money)
        self.item_names = {getattr(self.task, item).name: item for item in ITEMS}
        self.task.config = SimpleNamespace(
            config_name="offline_test",
            mystery_shop=SimpleNamespace(
                shop_config=SimpleNamespace(
                    **dict.fromkeys(FLAGS, False), time_of_mystery=time(0)
                ),
                invite_config=SimpleNamespace(friend_list_v=[]),
            ),
        )
        self.task._independent_schedule = MemorySchedule()
        self.task.set_next_run = Mock()
        self.task.start_time = self.current_date

    def clock_datetime(self):
        world = self

        class ClockMeta(type):
            def __instancecheck__(cls, instance):
                return isinstance(instance, datetime)

        class ClockDateTime(datetime, metaclass=ClockMeta):
            @classmethod
            def now(cls, tz=None):
                return world.current_date + timedelta(seconds=world.now - 1)

        return ClockDateTime

    def match_dynamic_template(self, template, image, roi_back, threshold,
                               frame_id=None, name=None):
        """Use the production comparison metric locally instead of image RPC."""
        x, y, width, height = roi_back
        region = image[y:y + height, x:x + width]
        scores = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
        _, maximum, _, location = cv2.minMaxLoc(scores)
        return {
            "matched": maximum > threshold,
            "roi_front": [x + location[0], y + location[1],
                          template.shape[1], template.shape[0]],
        }

    def match_rule(self, rule_data, image, frame_id=None, threshold=None):
        """Real static templates and ROIs, replacing only the transport layer."""
        if rule_data["method"] != "Template matching":
            raise AssertionError("Replay only supports the shop's template rules")
        file = rule_data["file"]
        if file not in self.template_cache:
            self.template_cache[file] = read_rgb_image(file)
        template = self.template_cache[file]
        x, y, width, height = rule_data["roi_back"]
        region = image[y:y + height, x:x + width]
        if (region.shape[0] < template.shape[0]
                or region.shape[1] < template.shape[1]):
            return {"matched": False}
        return self.match_dynamic_template(
            template, image, rule_data["roi_back"],
            rule_data["threshold"] if threshold is None else threshold,
        )

    def use_real_recognition(self):
        self.task.interval_timer = {}
        self.task.device.get_image_batch_cache = lambda target, frame_id=None: None
        appear = function_from_file(
            ROOT / "tasks/base_task.py", "appear",
            self.namespace, class_name="BaseTask"
        )
        self.task.appear = Mock(wraps=MethodType(appear, self.task))

    def sleep(self, seconds):
        self.operations.append(("sleep", seconds))
        self.now += seconds

    def screenshot(self):
        self.now += 0.1
        self.screenshot_count += 1
        frame = self.frames[self.page]
        if self.unstable:
            frame = np.roll(frame, self.screenshot_count * 17, axis=1)
        self.task.device.image = frame.copy()
        return self.task.device.image

    def swipe_adb(self, p1, p2, duration=0.1):
        self.operations.append(("adb", duration))
        self.now += duration
        direction = "down" if p1[1] > p2[1] else "up"
        if self.reject_gestures or (direction == "down" and self.reject_next_down):
            if direction == "down" and self.reject_next_down:
                self.reject_next_down -= 1
            self.failed_gestures.append((direction, self.page))
            raise self.namespace["GameStuckError"]("ADB gesture failed")
        old_page = self.page
        if direction == "down":
            self.page = min(self.page + 1, len(self.pages) - 1)
        else:
            self.page = max(self.page - 1, 0)
        self.gestures.append((direction, old_page, self.page))
        # Production swipe_adb returns None after a successful command.
        return None

    def appear(self, rule, **kwargs):
        if self.popup and rule.name == getattr(self.task, self.popup).name:
            return True
        if rule in (self.task.I_MS_SHARE, self.task.I_MS_BEFORE, self.task.I_MS_NEXT):
            return self.shop_visible
        item = self.item_names.get(rule.name)
        return item in self.pages[self.page]

    def remain(self, rule):
        return self.pages[self.page].get(self.item_names[rule.name], 0)

    def purchase(self, rule, check):
        item = self.item_names[rule.name]
        if self.pages[self.page].get(item, 0) <= 0:
            raise AssertionError("Attempted to purchase an exhausted item")
        if self.money < PRICES[item]:
            raise AssertionError("Attempted to purchase without enough currency")
        self.pages[self.page][item] -= 1
        self.money -= PRICES[item]
        self.purchases.append((self.page, item))
        return True

    def run(self, **enabled):
        config = SimpleNamespace(
            **{flag: enabled.get(flag, False) for flag in FLAGS},
            time_of_mystery=time(0),
        )
        self.task.config.mystery_shop.shop_config = config
        self.task.run_shop(config)


def read_rgb_image(path):
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise AssertionError(f"Could not read replay image: {path}")
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def place_rule_template(image, rule):
    template = read_rgb_image(ROOT / rule.file)
    x, y = rule.roi_front[:2]
    height, width = template.shape[:2]
    image[y:y + height, x:x + width] = template


def independent_schedule_class():
    """Load the actual store and atomic JSON helpers without the app logger."""
    atomic_path = ROOT / "module/config/atomicwrites.py"
    atomic_namespace = {"__name__": "_mystery_test_atomicwrites", "__file__": str(atomic_path)}
    exec(compile(atomic_path.read_text(encoding="utf-8"),
                 str(atomic_path), "exec"), atomic_namespace)
    schedule_path = ROOT / "tasks/MysteryShop/schedule.py"
    namespace = {
        "__file__": str(schedule_path),
        "datetime": datetime,
        "timedelta": timedelta,
        "sha256": sha256,
        "Path": Path,
        "os": os,
        "json": json,
        "FileLock": FileLock,
        "atomic_write": atomic_namespace["atomic_write"],
        "logger": Mock(),
    }
    for name in ("read_file", "write_file"):
        function_from_file(ROOT / "module/config/utils.py", name, namespace)
    return class_from_file(schedule_path, "MysteryShopSchedule", namespace)


class MysteryShopScrollTests(unittest.TestCase):
    def test_kekkai_adb_drag_clears_records_and_waits_two_seconds(self):
        world = ShopWorld([{}, {}])
        self.assertTrue(world.task._swipe_shop(world.task.S_MS_DOWN))
        world.task.swipe.assert_not_called()
        world.task.device.swipe_adb.assert_called_once_with((604, 464), (604, 264), duration=2)
        world.task.device.click_record_clear.assert_called_once_with()
        start = world.operations.index(("adb", 2))
        self.assertEqual(world.operations[start:start + 3], [("adb", 2), ("clear",), ("sleep", 2)])

    def test_rewind_uses_same_adb_drag_in_reverse(self):
        world = ShopWorld([{}, {}], start_page=1)
        self.assertTrue(world.task._swipe_shop(world.task.S_MS_TO_TOP))
        world.task.device.swipe_adb.assert_called_once_with((604, 264), (604, 464), duration=2)
        world.task.swipe.assert_not_called()

    def test_real_adb_method_receives_two_thousand_milliseconds(self):
        world = ShopWorld([{}])
        adb = function_from_file(
            ROOT / "module/device/method/adb.py", "swipe_adb",
            {"retry": lambda method: method}, class_name="Adb"
        )
        shell_device = SimpleNamespace(adb_shell=Mock())
        world.task.device.swipe_adb = Mock(wraps=MethodType(adb, shell_device))
        self.assertFalse(world.task._swipe_shop(world.task.S_MS_DOWN))
        shell_device.adb_shell.assert_called_once_with(["input", "swipe", 604, 464, 604, 264, 2000])
        world.task.device.click_record_clear.assert_called_once_with()

    def test_adb_error_cannot_be_reported_as_shelf_bottom(self):
        world = ShopWorld([{}])
        world.task.device.swipe_adb.side_effect = OSError("ADB disconnected")
        with self.assertRaises(OSError):
            world.task._swipe_shop(world.task.S_MS_DOWN)
        world.task.device.click_record_clear.assert_not_called()
        self.assertNotIn(("sleep", 2), world.operations)

    def test_lower_screen_target_is_bought(self):
        world = ShopWorld([{}, {"I_MS_BLUE": 1}])
        world.run(mystery_amulet=True)
        self.assertEqual(world.purchases, [(1, "I_MS_BLUE")])

    def test_same_item_on_different_screens_is_not_globally_skipped(self):
        world = ShopWorld([{"I_MS_BLUE": 1}, {"I_MS_BLUE": 1}])
        world.run(mystery_amulet=True)
        self.assertEqual(world.purchases, [(0, "I_MS_BLUE"), (1, "I_MS_BLUE")])

    def test_each_account_uses_its_own_purchase_switches(self):
        for account, enabled, expected in (
            ("oas1", dict(mystery_amulet=True, black_daruma_scrap=True,
                          shop_kaiko_3=True, shop_kaiko_4=True), set(ITEMS)),
            ("oas2", dict(black_daruma_scrap=True), {"I_MS_BLACK"}),
        ):
            with self.subTest(account=account):
                world = ShopWorld([{}, dict.fromkeys(ITEMS, 1)])
                world.run(**enabled)
                self.assertEqual({item for _, item in world.purchases}, expected)

    def test_all_switches_off_does_not_scroll_or_buy(self):
        world = ShopWorld([dict.fromkeys(ITEMS, 1), {}], start_page=1)
        world.run()
        world.task.device.swipe_adb.assert_not_called()
        world.task.buy_one.assert_not_called()

    def test_bottom_needs_two_successful_unchanged_gestures(self):
        world = ShopWorld([{"I_MS_BLUE": 1}])
        world.run(mystery_amulet=True)
        down_at_bottom = [
            gesture for gesture in world.gestures
            if gesture[0] == "down" and gesture[1] == gesture[2]
        ]
        self.assertEqual(len(down_at_bottom), 2)
        self.assertEqual(world.purchases, [(0, "I_MS_BLUE")])

    def test_failed_gesture_cannot_be_treated_as_reaching_bottom(self):
        world = ShopWorld([{}, {"I_MS_BLUE": 1}])
        world.reject_next_down = 1
        with self.assertRaises(world.namespace["GameStuckError"]):
            world.run(mystery_amulet=True)
        self.assertEqual(world.purchases, [])
        self.assertTrue(world.failed_gestures)

    def test_new_shop_rewinds_from_inherited_scroll_position(self):
        world = ShopWorld([{"I_MS_BLUE": 1}, {}, {}], start_page=2)
        world.run(mystery_amulet=True)
        self.assertEqual(world.purchases, [(0, "I_MS_BLUE")])
        world.pages[0]["I_MS_BLUE"] = 1
        self.assertEqual(world.page, 2)
        world.run(mystery_amulet=True)
        self.assertEqual(world.purchases, [(0, "I_MS_BLUE"), (0, "I_MS_BLUE")])

    def test_unbounded_list_aborts_instead_of_reporting_complete(self):
        world = ShopWorld([{} for _ in range(20)])
        with self.assertRaises(world.namespace["GameStuckError"]):
            world.run(mystery_amulet=True)
        self.assertLessEqual(
            len([gesture for gesture in world.gestures if gesture[0] == "down"]), 8
        )

    def test_unstable_screen_aborts_without_buying(self):
        world = ShopWorld([{"I_MS_BLUE": 1}])
        world.unstable = True
        with self.assertRaises(world.namespace["GameStuckError"]):
            world.run(mystery_amulet=True)
        self.assertEqual(world.purchases, [])

    def test_repeatedly_rejected_gestures_abort(self):
        world = ShopWorld([{}, {"I_MS_BLUE": 1}])
        world.reject_gestures = True
        with self.assertRaises(world.namespace["GameStuckError"]):
            world.run(mystery_amulet=True)
        self.assertEqual(world.purchases, [])
        self.assertEqual(world.gestures, [])

    def test_known_popups_are_not_mistaken_for_stable_shelves(self):
        for popup in ("I_BUY_PLUS", "I_BUY_SUCCESS",
                      "I_UI_REWARD", "I_INVITE_ENSURE"):
            with self.subTest(popup=popup):
                world = ShopWorld([{"I_MS_BLUE": 1}])
                world.popup = popup
                with self.assertRaises(world.namespace["GameStuckError"]):
                    world.run(mystery_amulet=True)
                world.task.device.swipe_adb.assert_not_called()
                world.task.buy_one.assert_not_called()

    def test_missing_shop_anchors_abort_before_gestures_or_purchases(self):
        world = ShopWorld([{"I_MS_BLUE": 1}])
        world.shop_visible = False
        with self.assertRaises(world.namespace["GameStuckError"]):
            world.run(mystery_amulet=True)
        world.task.device.swipe_adb.assert_not_called()
        world.task.buy_one.assert_not_called()

    def test_currency_symbol_on_shelf_does_not_block_real_recognition(self):
        world = ShopWorld([{}])
        frame = world.frames[0].copy()
        place_rule_template(frame, world.task.I_MS_SHARE)
        place_rule_template(frame, world.task.I_BUY_RMB)
        world.frames[0] = frame
        world.use_real_recognition()
        world.screenshot()
        self.assertTrue(world.task.appear(world.task.I_BUY_RMB))
        self.assertTrue(world.task.appear(world.task.I_MS_SHARE))
        self.assertFalse(world.task._swipe_shop(world.task.S_MS_DOWN))
        self.assertEqual(world.gestures, [("down", 0, 0)])

    def test_small_shelf_jitter_is_stable_but_large_scroll_is_not(self):
        world = ShopWorld([{}])
        before = world.task._shop_template()
        x, y, width, height = world.task.C_MS_GOODS.roi_back
        base = world.frames[0]
        jittered = base.copy()
        jittered[y + 2:y + height, x + 2:x + width] = (
            base[y:y + height - 2, x:x + width - 2]
        )
        world.task.device.image = jittered
        self.assertTrue(world.task._shop_frame_matches(before, threshold=0.985))
        scrolled = base.copy()
        scrolled[y:y + height, x:x + width] = np.roll(
            base[y:y + height, x:x + width], -200, axis=0
        )
        world.task.device.image = scrolled
        self.assertFalse(world.task._shop_frame_matches(before, threshold=0.985))

    def test_cached_assets_without_new_scroll_members_still_work(self):
        world = ShopWorld([{}, {"I_MS_BLUE": 1}])
        for name in ("C_MS_GOODS", "S_MS_DOWN", "S_MS_TO_TOP"):
            if name in vars(world.namespace["_Assets"]):
                delattr(world.namespace["_Assets"], name)
        world.run(mystery_amulet=True)
        self.assertEqual(world.purchases, [(1, "I_MS_BLUE")])


class MysteryShopScreenshotReplayTests(unittest.TestCase):
    def test_local_error_images_reach_the_scroll_gesture(self):
        supplied = os.environ.get("MYSTERY_SHOP_REPLAY_IMAGES", "")
        paths = [Path(path) for path in supplied.split(os.pathsep) if path]
        if not paths:
            self.skipTest("Set MYSTERY_SHOP_REPLAY_IMAGES for private local PNG replay")
        for path in paths:
            with self.subTest(image=path.name):
                world = ShopWorld([{}])
                world.frames[0] = read_rgb_image(path)
                world.use_real_recognition()
                # Actual BaseTask.appear -> RuleImage.match -> cv2, no anchor mocks.
                self.assertFalse(world.task._swipe_shop(world.task.S_MS_DOWN))
                self.assertEqual(world.gestures, [("down", 0, 0)])


class MysteryShopScheduleTests(unittest.TestCase):
    def assert_next_run(self, now, configured_time, success, expected):
        world = ShopWorld([{}])
        world.current_date = now
        world.task.config.mystery_shop.shop_config.time_of_mystery = configured_time
        with self.assertRaises(world.namespace["TaskEnd"]):
            world.task.next_time(success)
        world.task.set_next_run.assert_called_once()
        scheduled = world.task.set_next_run.call_args.kwargs
        self.assertEqual(scheduled["target"], expected)
        self.assertIsNone(scheduled.get("success"))
        self.assertIs(scheduled["server"], False)
        self.assertTrue(scheduled["finish"])
        self.assertEqual(scheduled["task"], "MysteryShop")
        self.assertIn(scheduled["target"].weekday(), (2, 5))
        self.assertGreater(scheduled["target"], now)
        if success:
            world.task._independent_schedule.write_next_run.assert_called_once_with(expected)
        else:
            world.task._independent_schedule.write_next_run.assert_not_called()

    def test_all_weekdays_schedule_next_open_day_at_configured_time(self):
        first_monday = datetime(2026, 9, 7, 12)
        expected_days = (9, 9, 12, 12, 12, 16, 16)
        for offset, day in enumerate(expected_days):
            for configured_time in (time(0), time(6)):
                for success in (False, True):
                    with self.subTest(weekday=offset, hour=configured_time.hour,
                                      success=success):
                        self.assert_next_run(
                            first_monday + timedelta(days=offset),
                            configured_time, success,
                            datetime(2026, 9, day, configured_time.hour),
                        )

    def test_failed_run_before_open_day_time_can_retry_today(self):
        self.assert_next_run(datetime(2026, 9, 9, 3), time(6), False,
                             datetime(2026, 9, 9, 6))
        self.assert_next_run(datetime(2026, 9, 9, 3), time(6), True,
                             datetime(2026, 9, 12, 6))

    def test_midnight_exact_match_is_not_rescheduled_in_the_past(self):
        self.assert_next_run(datetime(2026, 9, 9), time(0), False,
                             datetime(2026, 9, 12))

    def test_month_and_year_boundaries(self):
        self.assert_next_run(datetime(2026, 9, 30, 23, 59), time(0), True,
                             datetime(2026, 10, 3))
        self.assert_next_run(datetime(2026, 12, 31, 23, 59), time(6), False,
                             datetime(2027, 1, 2, 6))

    def test_real_task_delay_preserves_explicit_open_day_target(self):
        for success in (False, True):
            for server_time in (time(9), time(6)):
                with self.subTest(success=success, server_time=server_time):
                    world = ShopWorld([{}])
                    world.current_date = datetime(2026, 9, 5, 12)
                    world.task.config.mystery_shop.shop_config.time_of_mystery = time(6)
                    scheduler = SimpleNamespace(
                        success_interval=timedelta(days=1),
                        failure_interval=timedelta(days=1),
                        server_update=server_time,
                        float_time=time(0, 10),
                        delay_date=1,
                        next_run=None,
                    )
                    config = SimpleNamespace(
                        model=SimpleNamespace(
                            mystery_shop=SimpleNamespace(scheduler=scheduler)
                        ),
                        reload=Mock(),
                        save=Mock(),
                        lock_config=Mock(),
                    )
                    namespace = dict(world.namespace)
                    namespace["random"] = SimpleNamespace(
                        randint=lambda lower, upper: upper
                    )
                    for name in ("convert_to_underscore", "nearest_future",
                                 "dict_to_kv", "parse_tomorrow_server"):
                        function_from_file(
                            ROOT / "module/config/utils.py", name, namespace
                        )
                    delay = function_from_file(
                        ROOT / "module/config/config.py", "task_delay",
                        namespace, class_name="Config"
                    )
                    world.task.config.task_delay = MethodType(delay, config)
                    set_next_run = function_from_file(
                        ROOT / "tasks/base_task.py", "set_next_run",
                        namespace, class_name="BaseTask"
                    )
                    world.task.set_next_run = MethodType(set_next_run, world.task)
                    with self.assertRaises(world.namespace["TaskEnd"]):
                        world.task.next_time(success)
                    self.assertEqual(scheduler.next_run, datetime(2026, 9, 9, 6))
                    config.reload.assert_called_once_with()
                    config.save.assert_called_once_with()
                    config.lock_config.acquire.assert_called_once_with()
                    config.lock_config.release.assert_called_once_with()

    def test_closed_weekdays_exit_before_any_shop_interaction(self):
        for offset in (0, 1, 3, 4, 6):
            with self.subTest(weekday=offset):
                world = ShopWorld([{"I_MS_BLUE": 1}])
                world.current_date = datetime(2026, 9, 7, 12) + timedelta(days=offset)
                world.task.goto_page = Mock()
                world.task.ui_click = Mock()
                with self.assertRaises(world.namespace["TaskEnd"]):
                    world.task.run()
                world.task.goto_page.assert_not_called()
                world.task.ui_click.assert_not_called()
                world.task.device.swipe_adb.assert_not_called()

    def test_open_weekdays_pass_the_gate(self):
        for day in (9, 12):
            with self.subTest(day=day):
                world = ShopWorld([{}])
                world.current_date = datetime(2026, 9, day, 12)
                world.task._ensure_shop_open()
                world.task.set_next_run.assert_not_called()

    def test_stale_start_time_cannot_bypass_current_weekday(self):
        world = ShopWorld([{"I_MS_BLUE": 1}])
        world.task.start_time = datetime(2026, 9, 9, 23, 59)
        world.current_date = datetime(2026, 9, 10, 0, 1)
        world.task.goto_page = Mock()
        with self.assertRaises(world.namespace["TaskEnd"]):
            world.task.run()
        world.task.goto_page.assert_not_called()

    def test_midnight_rollover_stops_purchase_and_swipe(self):
        world = ShopWorld([{"I_MS_BLUE": 1}])
        world.current_date = datetime(2026, 9, 9, 23, 59, 59)
        world.now = 3.0
        with self.assertRaises(world.namespace["TaskEnd"]):
            world.run(mystery_amulet=True)
        world.task.device.swipe_adb.assert_not_called()
        world.task.buy_one.assert_not_called()

    def test_early_external_reschedule_restores_saved_time_without_interacting(self):
        world = ShopWorld([{"I_MS_BLUE": 1}])
        saved = datetime(2026, 9, 9, 6)
        world.task._independent_schedule.next_run = saved
        world.task.config.mystery_shop.scheduler = SimpleNamespace(
            next_run=datetime(2026, 9, 4),
            success_interval=timedelta(seconds=1),
            failure_interval=timedelta(seconds=1),
        )
        world.task.config.mystery_shop.shop_config.time_of_mystery = time(0)
        world.task.goto_page = Mock()
        world.task.ui_click = Mock()
        with self.assertRaises(world.namespace["TaskEnd"]):
            world.task.run()
        world.task.set_next_run.assert_called_once_with(
            task="MysteryShop", target=saved, server=False, finish=True
        )
        world.task.goto_page.assert_not_called()
        world.task.ui_click.assert_not_called()
        world.task.device.swipe_adb.assert_not_called()
        world.task.buy_one.assert_not_called()
        world.task._independent_schedule.write_next_run.assert_not_called()

    def test_saved_open_time_reached_allows_shop_entry(self):
        world = ShopWorld([{}])
        saved = datetime(2026, 9, 9, 6)
        world.task._independent_schedule.next_run = saved
        world.current_date = saved
        world.task._ensure_shop_due()
        world.task.set_next_run.assert_not_called()

    def test_failed_run_does_not_replace_successful_independent_state(self):
        world = ShopWorld([{}])
        saved = datetime(2026, 9, 9, 6)
        world.task._independent_schedule.next_run = saved
        with self.assertRaises(world.namespace["TaskEnd"]):
            world.task.next_time(False)
        self.assertEqual(world.task._independent_schedule.next_run, saved)
        world.task._independent_schedule.write_next_run.assert_not_called()

    def test_independent_state_write_failure_stops_before_scheduling(self):
        world = ShopWorld([{}])
        world.task._independent_schedule.write_next_run.side_effect = OSError(
            "read-only independent schedule"
        )
        with self.assertRaises(world.namespace["RequestHumanTakeover"]):
            world.task.next_time(True)
        world.task.set_next_run.assert_not_called()

    def test_independent_state_read_failure_stops_before_shop_entry(self):
        world = ShopWorld([{}])
        world.task._independent_schedule.read_next_run.side_effect = ValueError(
            "damaged independent schedule"
        )
        world.task.goto_page = Mock()
        with self.assertRaises(world.namespace["RequestHumanTakeover"]):
            world.task.run()
        world.task.goto_page.assert_not_called()
        world.task.set_next_run.assert_not_called()


class MysteryShopIndependentStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mystery-shop-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store_class = independent_schedule_class()

    def make_world(self, config_name="oas1", root=None):
        world = ShopWorld([{}])
        world.task.config.config_name = config_name
        world.task._independent_schedule = self.store_class(
            config_name, root=self.root if root is None else root
        )
        return world

    def test_missing_state_allows_first_open_day_without_creating_a_file(self):
        world = self.make_world()
        self.assertIsNone(world.task._independent_schedule.read_next_run())
        world.task._ensure_shop_due()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_restart_preserves_due_time_despite_external_config_changes(self):
        first = self.make_world()
        with self.assertRaises(first.namespace["TaskEnd"]):
            first.task.next_time(True)
        saved = first.task._independent_schedule.read_next_run()
        self.assertEqual(saved, datetime(2026, 9, 9))
        restarted = self.make_world()
        restarted.task.config.mystery_shop.shop_config.time_of_mystery = time(18)
        restarted.task.config.mystery_shop.scheduler = SimpleNamespace(
            next_run=datetime(2026, 9, 4),
            success_interval=timedelta(seconds=1),
            failure_interval=timedelta(seconds=1),
        )
        restarted.task.goto_page = Mock()
        with self.assertRaises(restarted.namespace["TaskEnd"]):
            restarted.task.run()
        restarted.task.goto_page.assert_not_called()
        restarted.task.set_next_run.assert_called_once_with(
            task="MysteryShop", target=saved, server=False, finish=True
        )

    def test_account_state_is_isolated(self):
        oas1 = self.make_world("oas1")
        oas2 = self.make_world("oas2")
        first_target = datetime(2026, 9, 9)
        second_target = datetime(2026, 9, 12, 6)
        oas1.task._independent_schedule.write_next_run(first_target)
        self.assertIsNone(oas2.task._independent_schedule.read_next_run())
        oas2.task._ensure_shop_due()
        oas2.task._independent_schedule.write_next_run(second_target)
        self.assertEqual(oas1.task._independent_schedule.read_next_run(), first_target)
        self.assertEqual(oas2.task._independent_schedule.read_next_run(), second_target)
        self.assertNotEqual(oas1.task._independent_schedule.path,
                            oas2.task._independent_schedule.path)

    def test_failed_task_does_not_persist_success_state(self):
        world = self.make_world()
        with self.assertRaises(world.namespace["TaskEnd"]):
            world.task.next_time(False)
        self.assertIsNone(world.task._independent_schedule.read_next_run())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_corrupt_json_blocks_task_before_any_interaction(self):
        world = self.make_world()
        world.task._independent_schedule.path.write_text("{broken", encoding="utf-8")
        world.task.goto_page = Mock()
        with self.assertRaises(world.namespace["RequestHumanTakeover"]):
            world.task.run()
        world.task.goto_page.assert_not_called()
        world.task.device.swipe_adb.assert_not_called()
        world.task.set_next_run.assert_not_called()

    def test_actual_state_write_failure_is_not_reported_as_completion(self):
        blocked_directory = self.root / "not-a-directory"
        blocked_directory.write_text("fixture", encoding="utf-8")
        world = self.make_world(root=blocked_directory)
        with self.assertRaises(world.namespace["RequestHumanTakeover"]):
            world.task.next_time(True)
        world.task.set_next_run.assert_not_called()

    def make_queue(self, world, outer_time, enabled=True):
        store = world.task._independent_schedule
        namespace = dict(world.namespace)
        factory = Mock(return_value=store)
        namespace.update({
            "operator": operator,
            "MysteryShopSchedule": factory,
            "ConfigModel": SimpleNamespace(
                type=lambda name: {"mystery_shop": "MysteryShop", "soul_zone": "SoulZone"}[name]
            ),
            "TaskScheduler": SimpleNamespace(schedule=lambda rule, pending: pending),
            "DEFAULT_TIME": datetime(2023, 1, 1),
        })
        class_from_file(ROOT / "module/config/config.py", "Function", namespace)
        source_data = {
            "mystery_shop": {"scheduler": {
                "enable": enabled, "next_run": outer_time, "priority": 1,
            }},
            "soul_zone": {"scheduler": {
                "enable": True, "next_run": datetime(2026, 9, 4), "priority": 2,
            }},
        }
        config = SimpleNamespace(
            config_name=world.task.config.config_name,
            model=SimpleNamespace(
                dict=Mock(return_value=source_data),
                running_task=None,
                script=SimpleNamespace(optimization=SimpleNamespace(schedule_rule="test")),
            ),
            save=Mock(),
        )
        update = function_from_file(
            ROOT / "module/config/config.py", "update_scheduler",
            namespace, class_name="Config"
        )
        real_update = MethodType(update, config)
        schedule_module = ModuleType("tasks.MysteryShop.schedule")
        schedule_module.MysteryShopSchedule = factory

        def update_with_temporary_store():
            # Production imports the store lazily inside update_scheduler.
            with patch.dict(sys.modules, {"tasks.MysteryShop.schedule": schedule_module}):
                return real_update()

        config.update_scheduler = update_with_temporary_store
        return config, factory, source_data

    def test_queue_uses_independent_time_without_writing_or_delaying_other_tasks(self):
        world = self.make_world()
        store = world.task._independent_schedule
        saved = datetime(2026, 9, 9, 6)
        store.write_next_run(saved)
        original_state = store.path.read_bytes()
        store.write_next_run = Mock(wraps=store.write_next_run)
        outer = datetime(2026, 9, 4)
        config, factory, source_data = self.make_queue(world, outer)
        config.update_scheduler()
        self.assertEqual([task.command for task in config.pending_task], ["SoulZone"])
        self.assertEqual(
            [(task.command, task.next_run) for task in config.waiting_task],
            [("MysteryShop", saved)],
        )
        self.assertEqual(source_data["mystery_shop"]["scheduler"]["next_run"], outer)
        self.assertEqual(store.path.read_bytes(), original_state)
        store.write_next_run.assert_not_called()
        config.save.assert_not_called()
        factory.assert_called_once_with("oas1")

    def test_expired_state_projects_to_next_open_day_without_persisting(self):
        world = self.make_world()
        world.current_date = datetime(2026, 9, 11, 12)
        store = world.task._independent_schedule
        expired = datetime(2026, 9, 9, 6)
        store.write_next_run(expired)
        original_state = store.path.read_bytes()
        config, _, _ = self.make_queue(world, datetime(2026, 9, 4))
        config.update_scheduler()
        self.assertEqual(
            [(task.command, task.next_run) for task in config.waiting_task],
            [("MysteryShop", datetime(2026, 9, 12, 6))],
        )
        self.assertEqual(store.path.read_bytes(), original_state)
        self.assertEqual(store.read_next_run(), expired)

    def test_due_independent_time_overrides_later_external_time(self):
        world = self.make_world()
        world.current_date = datetime(2026, 9, 12, 12)
        world.task._independent_schedule.write_next_run(datetime(2026, 9, 9, 6))
        config, _, _ = self.make_queue(world, datetime(2026, 10, 1))
        config.update_scheduler()
        pending = {task.command: task.next_run for task in config.pending_task}
        self.assertEqual(pending["MysteryShop"], datetime(2026, 9, 12, 6))
        self.assertEqual(pending["SoulZone"], datetime(2026, 9, 4))
        self.assertEqual(config.waiting_task, [])

    def test_corrupt_independent_state_does_not_break_other_task_queue(self):
        world = self.make_world()
        world.task._independent_schedule.path.write_text("{broken", encoding="utf-8")
        config, _, _ = self.make_queue(world, datetime(2026, 9, 4))
        config.update_scheduler()
        self.assertEqual(
            {task.command for task in config.pending_task}, {"MysteryShop", "SoulZone"}
        )
        config.save.assert_not_called()
        world.namespace["logger"].warning.assert_called()

    def test_disabled_shop_does_not_read_independent_state(self):
        world = self.make_world()
        world.task._independent_schedule.path.write_text("{broken", encoding="utf-8")
        config, factory, _ = self.make_queue(world, datetime(2026, 9, 4), enabled=False)
        config.update_scheduler()
        self.assertEqual([task.command for task in config.pending_task], ["SoulZone"])
        factory.assert_not_called()

    def test_missing_independent_state_keeps_external_queue_time(self):
        world = self.make_world()
        outer = datetime(2026, 9, 12, 6)
        config, _, _ = self.make_queue(world, outer)
        config.update_scheduler()
        self.assertEqual(
            [(task.command, task.next_run) for task in config.waiting_task],
            [("MysteryShop", outer)],
        )


if __name__ == "__main__":
    unittest.main()

"""Read-only emulator window selection shared by Handle and window actions."""

from dataclasses import dataclass
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from module.logger import logger


MUMU_DEVICES = {'mumunxdevice.exe', 'mumuplayer.exe', 'nemuplayer.exe'}
MODERN_MUMU = {'mumunxdevice.exe', 'mumuplayer.exe'}
RENDER_TITLES = {'MuMuNxDevice', 'MuMuPlayer', 'NemuPlayer', 'nemudisplay'}
INSTANCE_MARKER = re.compile(
    r'ShellWindowReveiceMessageWnd(MuMuPlayer(?:Global)?-\d+(?:\.\d+)*-\d+)'
)


@dataclass(frozen=True)
class EmulatorWindow:
    hwnd: int
    title: str
    pid: int
    process: str
    visible: bool
    iconic: bool
    owner: int = 0
    tool: bool = False
    children: tuple = ()
    ports: tuple = ()

    @property
    def is_mumu(self):
        return self.process.lower() in MUMU_DEVICES

    @property
    def is_device(self):
        if self.owner or self.tool:
            return False
        if self.is_mumu:
            return bool(RENDER_TITLES.intersection(self.children))
        # Managers/services may have exactly the same title as the game.
        return not self.process.lower().startswith(('mumu', 'nemu'))


def instance_ports(executable, markers):
    """Read custom/alias ports using a message window owned by this PID.

    Never guess instance 0 from missing command-line flags or a window title.
    Missing/ambiguous metadata means no verified serial binding.
    """
    names = {match.group(1) for title in markers
             if (match := INSTANCE_MARKER.fullmatch(title))}
    if len(names) != 1 or not executable:
        return ()
    name = names.pop()
    for parent in list(Path(executable).parents)[:5]:
        path = parent / 'vms' / name / (name + '.nemu')
        if not path.is_file():
            continue
        try:
            root = ET.parse(path).getroot()
            return tuple(sorted({int(node.attrib['hostport']) for node in root.iter()
                                 if node.tag.rsplit('}', 1)[-1] == 'Forwarding'
                                 and node.get('guestport') == '5555'
                                 and node.get('proto', '1') == '1'
                                 and node.get('hostport', '').isdigit()}))
        except (OSError, ET.ParseError, ValueError) as exc:
            logger.warning(f'[window] Cannot read instance ports: {path}: {exc}')
            return ()
    return ()


def enumerate_emulator_windows():
    """Metadata only: never show, hide, resize, or message a window."""
    import psutil
    import win32gui
    import win32process

    roots, markers, processes = [], {}, {}

    def collect(hwnd, _):
        try:
            pid = win32process.GetWindowThreadProcessId(hwnd)[1]
            title = win32gui.GetWindowText(hwnd)
            markers.setdefault(pid, []).append(title)
            roots.append((hwnd, pid, title))
        except win32gui.error:
            pass  # A window may disappear during enumeration.

    win32gui.EnumWindows(collect, None)
    result = []
    for hwnd, pid, title in roots:
        if not title or INSTANCE_MARKER.fullmatch(title):
            continue
        try:
            if pid not in processes:
                process = psutil.Process(pid)
                name = process.name()
                ports = instance_ports(process.exe(), markers.get(pid, ())) \
                    if name.lower() in MODERN_MUMU else ()
                processes[pid] = name, ports
            name, ports = processes[pid]
            children = []
            if name.lower() in MUMU_DEVICES:
                win32gui.EnumChildWindows(
                    hwnd, lambda child, _: children.append(win32gui.GetWindowText(child)), None
                )
            result.append(EmulatorWindow(
                hwnd=hwnd, title=title, pid=pid, process=name,
                visible=bool(win32gui.IsWindowVisible(hwnd)),
                iconic=bool(win32gui.IsIconic(hwnd)),
                owner=win32gui.GetWindow(hwnd, 4),  # GW_OWNER
                tool=bool(win32gui.GetWindowLong(hwnd, -20) & 0x80),  # WS_EX_TOOLWINDOW
                children=tuple(children), ports=ports,
            ))
        except (psutil.Error, win32gui.error, OSError):
            continue
    return result


def local_adb_port(serial):
    match = re.fullmatch(r'(?:127\.0\.0\.1|localhost):([0-9]+)', str(serial), re.I)
    if match:
        port = int(match.group(1))
        return port if 0 < port <= 65535 else None
    match = re.fullmatch(r'emulator-([0-9]+)', str(serial))
    if match:
        port = int(match.group(1)) + 1
        return port if 0 < port <= 65535 else None
    return None


def select_emulator_window(windows, window_name, serial=''):
    """Pure selection: explicit HWND, auto, or title; never first-match wins."""
    name = str(window_name or '').strip()
    if not name:
        return None
    candidates = [window for window in windows if window.is_device]
    if name.isdecimal():
        candidates = [window for window in candidates if window.hwnd == int(name)]
    elif name.lower() == 'auto':
        candidates = [window for window in candidates if window.is_mumu or
                      any(key in window.title for key in ('雷电', '夜神', '蓝叠', '逍遥', '模拟器'))]
    else:
        candidates = [window for window in candidates if name.lower() in window.title.lower()]
    # Serial is authoritative for modern MuMu, even for explicit HWNDs.
    if serial and serial != 'auto':
        port = local_adb_port(serial)
        candidates = [window for window in candidates
                      if window.process.lower() not in MODERN_MUMU or
                      (port is not None and port in window.ports)]
    # Shared compatibility ports (e.g. 7555) can occur in two instances. Even
    # an exact title must not break a tie between serial-matched MuMu devices.
    if len([window for window in candidates if window.process.lower() in MODERN_MUMU]) > 1:
        return None
    if not name.isdecimal() and name.lower() != 'auto':
        exact = [window for window in candidates if window.title.casefold() == name.casefold()]
        if exact:
            candidates = exact
    # Never reveal generic hidden helpers; allow a serial-bound MuMu device
    # with a verified render tree to return from background mode.
    candidates = [window for window in candidates if window.visible or
                  (window.is_mumu and local_adb_port(serial) in window.ports)]
    return candidates[0] if len(candidates) == 1 else None


def resolve_emulator_window(window_name, serial=''):
    window = select_emulator_window(enumerate_emulator_windows(), window_name, serial)
    if window is None:
        logger.warning(f'[window] No unique verified emulator window; skip: '
                       f'name={window_name!r}, serial={serial!r}')
    else:
        logger.info(f'[window] Selected hwnd={window.hwnd}, pid={window.pid}, '
                    f'process={window.process}, title={window.title!r}, serial={serial!r}, '
                    f'visible={window.visible}, minimized={window.iconic}')
    return window


def change_emulator_window(window_name, serial='', action='show', convert_hidden=True):
    """Operate on one freshly verified device, never all title matches."""
    import win32gui
    import win32process

    window = resolve_emulator_window(window_name, serial)
    if window is None:
        return False
    try:
        # Protect against a window disappearing/reusing its HWND after discovery.
        if (not win32gui.IsWindow(window.hwnd)
                or win32process.GetWindowThreadProcessId(window.hwnd)[1] != window.pid
                or win32gui.GetWindowText(window.hwnd) != window.title):
            logger.warning('[window] Window changed after discovery; skip action')
            return False
        if action == 'show':
            win32gui.ShowWindow(window.hwnd, 9 if win32gui.IsIconic(window.hwnd) else 5)
            # Windows can deny foreground activation; the window is still shown.
            try:
                win32gui.SetForegroundWindow(window.hwnd)
            except win32gui.error:
                logger.info('[window] Foreground activation declined by Windows')
        elif action == 'hide':
            win32gui.ShowWindow(window.hwnd, 0)
        elif action == 'minimize':
            visible = win32gui.IsWindowVisible(window.hwnd)
            if not visible and not convert_hidden:
                return False
            win32gui.ShowWindow(window.hwnd, 6 if visible else 7)
        else:
            raise ValueError(f'Unknown window action: {action}')
        logger.info(f'[window] {action}: hwnd={window.hwnd}, pid={window.pid}, '
                    f'title={window.title!r}, serial={serial!r}')
        return True
    except win32gui.error as exc:
        logger.warning(f'[window] {action} failed safely: {exc}')
        return False

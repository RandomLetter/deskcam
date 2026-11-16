"""Desktop integration: pastes a picture into another app's chat box, and shortcuts that work from any app."""
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

MAC = sys.platform == "darwin"
APP_NAMES = {"claude": "Claude", "chatgpt": "ChatGPT"}


class SendError(OSError):
    """A problem the person can fix; its message is shown to them as is."""


def parse_shortcut(combo):
    """'Ctrl+Alt+KeyS' -> ('KeyS', ['Ctrl', 'Alt'])"""
    *mods, key = combo.split("+")
    return key, mods


def is_global(combo):
    """Shortcuts with Ctrl, Alt or Win, or on an F key, also work while another app is in front."""
    key, mods = parse_shortcut(combo)
    return bool({"Ctrl", "Alt", "Meta"} & set(mods)) or bool(re.fullmatch(r"F\d+", key))


if MAC:
    def foreground():
        return None

    def find_chat(target, preferred, skip_pids):
        """Returns (where, name) for the app to paste into."""
        if target in APP_NAMES:
            name = APP_NAMES[target]
            running = _osascript(
                "on run argv",
                'tell application "System Events" to return exists (first process whose name is (item 1 of argv))',
                "end run",
                args=[name],
            )
            if running != "true":
                raise SendError(f"The {name} app isn't open. Open your chat in it first.")
            return name, name
        # lsappinfo lists apps from most to least recently used.
        listing = subprocess.run(["lsappinfo", "visibleProcessList"], capture_output=True, encoding="utf-8").stdout
        for asn, name in re.findall(r'(ASN:0x[0-9a-fA-F]+-0x[0-9a-fA-F]+)-"([^"]*)"', listing):
            info = subprocess.run(["lsappinfo", "info", "-only", "pid", asn], capture_output=True, encoding="utf-8").stdout
            found = re.search(r"=\s*(\d+)", info)
            if found and int(found.group(1)) not in skip_pids and name != "DeskCam":
                return int(found.group(1)), name
        raise SendError("No other app is open to send to.")

    def paste_picture(where, png, rgba, width, height, message, enter, delay):
        picture = Path(tempfile.gettempdir()) / "deskcam-send.png"
        picture.write_bytes(png)
        activate = (
            'tell application "System Events" to set frontmost of (first process whose unix id is (item 2 of argv as integer)) to true'
            if isinstance(where, int) else "tell application (item 2 of argv) to activate"
        )
        _osascript(
            "on run argv",
            "set the clipboard to (read (POSIX file (item 1 of argv)) as «class PNGf»)",
            activate,
            "delay 0.4",
            'tell application "System Events" to keystroke "v" using command down',
            'if (item 3 of argv) is not "" then',
            "delay 0.3",
            'tell application "System Events" to keystroke (item 3 of argv)',
            "end if",
            'if (item 4 of argv) is "yes" then',
            "delay ((item 5 of argv as integer) / 1000)",
            'tell application "System Events" to key code 36',
            "end if",
            "end run",
            args=[str(picture), str(where), message, "yes" if enter else "no", str(int(delay * 1000))],
        )

    def _osascript(*lines, args=()):
        command = ["osascript"]
        for line in lines:
            command += ["-e", line]
        result = subprocess.run([*command, *args], capture_output=True, encoding="utf-8")
        if result.returncode:
            if re.search(r"1002|1743|not allowed|assistive", result.stderr):
                raise SendError("Allow DeskCam under System Settings → Privacy & Security → Accessibility, then try again.")
            raise SendError(result.stderr.strip() or "macOS couldn't paste the picture.")
        return result.stdout.strip()

    class Hotkeys:
        """Shortcuts from other apps aren't supported on the Mac yet; they all work inside DeskCam."""

        def __init__(self, on_press):
            self.status = {}

        def apply(self, shortcuts):
            self.status = {action: "app" for action in shortcuts}
            return self.status

else:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    HWND, UINT, DWORD = wintypes.HWND, wintypes.UINT, wintypes.DWORD
    for name, args, result in [
        ("EnumWindows", [WNDENUMPROC, wintypes.LPARAM], wintypes.BOOL),
        ("GetWindowTextLengthW", [HWND], ctypes.c_int),
        ("GetWindowTextW", [HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        ("GetClassNameW", [HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        ("IsWindowVisible", [HWND], wintypes.BOOL),
        ("IsIconic", [HWND], wintypes.BOOL),
        ("GetWindowLongW", [HWND, ctypes.c_int], wintypes.LONG),
        ("GetWindowThreadProcessId", [HWND, ctypes.POINTER(DWORD)], DWORD),
        ("GetForegroundWindow", [], HWND),
        ("SetForegroundWindow", [HWND], wintypes.BOOL),
        ("BringWindowToTop", [HWND], wintypes.BOOL),
        ("ShowWindow", [HWND, ctypes.c_int], wintypes.BOOL),
        ("AttachThreadInput", [DWORD, DWORD, wintypes.BOOL], wintypes.BOOL),
        ("SendInput", [UINT, ctypes.POINTER(INPUT), ctypes.c_int], UINT),
        ("GetAsyncKeyState", [ctypes.c_int], wintypes.SHORT),
        ("CreateWindowExW", [DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, DWORD, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID], HWND),
        ("DestroyWindow", [HWND], wintypes.BOOL),
        ("OpenClipboard", [HWND], wintypes.BOOL),
        ("EmptyClipboard", [], wintypes.BOOL),
        ("CloseClipboard", [], wintypes.BOOL),
        ("SetClipboardData", [UINT, wintypes.HANDLE], wintypes.HANDLE),
        ("RegisterClipboardFormatW", [wintypes.LPCWSTR], UINT),
        ("RegisterHotKey", [HWND, ctypes.c_int, UINT, UINT], wintypes.BOOL),
        ("UnregisterHotKey", [HWND, ctypes.c_int], wintypes.BOOL),
        ("GetMessageW", [ctypes.POINTER(wintypes.MSG), HWND, UINT, UINT], wintypes.BOOL),
        ("PeekMessageW", [ctypes.POINTER(wintypes.MSG), HWND, UINT, UINT, UINT], wintypes.BOOL),
        ("PostThreadMessageW", [DWORD, UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
    ]:
        function = getattr(user32, name)
        function.argtypes, function.restype = args, result
    kernel32.GetCurrentThreadId.restype = DWORD
    kernel32.GlobalAlloc.argtypes, kernel32.GlobalAlloc.restype = [UINT, ctypes.c_size_t], wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes, kernel32.GlobalLock.restype = [wintypes.HGLOBAL], ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    dwmapi.DwmGetWindowAttribute.argtypes = [HWND, DWORD, ctypes.c_void_p, DWORD]

    GWL_EXSTYLE, WS_EX_TOPMOST, WS_EX_TOOLWINDOW = -20, 0x8, 0x80
    DWMWA_CLOAKED = 14
    SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
    KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x2, 0x4
    VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN, VK_RETURN, VK_V = 0x10, 0x11, 0x12, 0x5B, 0x5C, 0x0D, 0x56
    CF_DIB, GMEM_MOVEABLE = 8, 0x2
    WM_HOTKEY, WM_APP = 0x312, 0x8000
    MOD_KEYS = {"Alt": 0x1, "Ctrl": 0x2, "Shift": 0x4, "Meta": 0x8}
    MOD_NOREPEAT = 0x4000
    VIRTUAL_KEYS = {
        **{f"Key{c}": ord(c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
        **{f"Digit{d}": ord(str(d)) for d in range(10)},
        **{f"Numpad{d}": 0x60 + d for d in range(10)},
        **{f"F{n}": 0x6F + n for n in range(1, 25)},
        "Space": 0x20, "Enter": 0x0D, "Tab": 0x09, "Backquote": 0xC0, "Minus": 0xBD, "Equal": 0xBB,
        "BracketLeft": 0xDB, "BracketRight": 0xDD, "Backslash": 0xDC, "Semicolon": 0xBA, "Quote": 0xDE,
        "Comma": 0xBC, "Period": 0xBE, "Slash": 0xBF, "ArrowLeft": 0x25, "ArrowUp": 0x26, "ArrowRight": 0x27,
        "ArrowDown": 0x28, "PageUp": 0x21, "PageDown": 0x22, "End": 0x23, "Home": 0x24, "Insert": 0x2D,
        "Delete": 0x2E, "Pause": 0x13,
    }

    def foreground():
        return user32.GetForegroundWindow()

    def _text(getter, hwnd, size=512):
        buffer = ctypes.create_unicode_buffer(size)
        getter(hwnd, buffer, size)
        return buffer.value

    def _pid(hwnd):
        pid = DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def _app_windows(skip_pids):
        """Visible app windows from front to back, as (hwnd, title, always_on_top)."""
        handles = []
        user32.EnumWindows(WNDENUMPROC(lambda hwnd, _: handles.append(hwnd) or True), 0)
        windows = []
        for hwnd in handles:
            title = _text(user32.GetWindowTextW, hwnd)
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            cloaked = DWORD()
            dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), 4)
            if (not title or title.startswith("DeskCam") or not user32.IsWindowVisible(hwnd) or cloaked.value
                    or style & WS_EX_TOOLWINDOW or _text(user32.GetClassNameW, hwnd) in SHELL_CLASSES
                    or _pid(hwnd) in skip_pids):
                continue
            windows.append((hwnd, title, bool(style & WS_EX_TOPMOST)))
        return windows

    def find_chat(target, preferred, skip_pids):
        """Returns (hwnd, title) of the window to paste into."""
        windows = _app_windows(skip_pids)
        if target in APP_NAMES:
            name = APP_NAMES[target]
            pattern = re.compile(rf"(^|\W){name}(\W|$)")
            for hwnd, title, _ in windows:
                if pattern.search(title):
                    return hwnd, title
            raise SendError(f"Couldn't find a {name} window. Open your chat in the {name} app or a browser tab first.")
        for hwnd, title, _ in windows:
            if hwnd == preferred:
                return hwnd, title
        # Always-on-top overlays sit in front of everything, but nobody chats in them.
        for hwnd, title, on_top in windows:
            if not on_top and not user32.IsIconic(hwnd):
                return hwnd, title
        raise SendError("No other window is open to send to.")

    def _send_keys(*events):
        """events: (virtual_key, flags) or (0, KEYEVENTF_UNICODE, char_code)."""
        inputs = (INPUT * len(events))()
        for item, (vk, flags, *scan) in zip(inputs, events):
            item.type = 1  # keyboard
            item.ki = KEYBDINPUT(vk, scan[0] if scan else 0, flags, 0, 0)
        user32.SendInput(len(events), inputs, ctypes.sizeof(INPUT))

    def _press(vk, *modifiers):
        _send_keys(*[(m, 0) for m in modifiers], (vk, 0), (vk, KEYEVENTF_KEYUP),
                   *[(m, KEYEVENTF_KEYUP) for m in reversed(modifiers)])

    def _type(text):
        units = struct.unpack(f"<{len(text.encode('utf-16-le')) // 2}H", text.encode("utf-16-le"))
        _send_keys(*[e for u in units for e in ((0, KEYEVENTF_UNICODE, u), (0, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, u))])

    def _wait_for_keys_up():
        """Ctrl or Alt still held from the shortcut would turn Ctrl+V into something else."""
        deadline = time.time() + 2
        while time.time() < deadline and any(
                user32.GetAsyncKeyState(vk) & 0x8000 for vk in (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN)):
            time.sleep(0.03)

    def _focus(hwnd):
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # restore
        # Windows only lets the app in front hand over focus, so borrow its input thread.
        mine = kernel32.GetCurrentThreadId()
        theirs = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        attached = theirs and theirs != mine and user32.AttachThreadInput(mine, theirs, True)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(mine, theirs, False)
        for _ in range(20):
            if user32.GetForegroundWindow() == hwnd:
                return
            time.sleep(0.05)
        raise SendError("Windows wouldn't bring the chat window to the front. Click it once, then try again.")

    def _copy_picture(png, rgba, width, height):
        """Puts the picture on the clipboard as PNG and as a bitmap, so any app can paste it."""
        bgra = bytearray(len(rgba))
        for to, frm in ((0, 2), (1, 1), (2, 0), (3, 3)):
            bgra[to::4] = rgba[frm::4]
        stride = width * 4
        pixels = b"".join(bgra[row:row + stride] for row in range((height - 1) * stride, -1, -stride))
        header = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 32, 0, len(pixels), 2835, 2835, 0, 0)
        formats = {user32.RegisterClipboardFormatW("PNG"): png, CF_DIB: header + pixels}
        owner = user32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, None, None, None, None)
        try:
            for _ in range(20):
                if user32.OpenClipboard(owner):
                    break
                time.sleep(0.05)
            else:
                raise SendError("Another app is holding the clipboard. Try again.")
            try:
                user32.EmptyClipboard()
                for kind, data in formats.items():
                    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                    ctypes.memmove(kernel32.GlobalLock(handle), data, len(data))
                    kernel32.GlobalUnlock(handle)
                    if not user32.SetClipboardData(kind, handle):
                        kernel32.GlobalFree(handle)
                        raise SendError("Couldn't copy the picture to the clipboard.")
            finally:
                user32.CloseClipboard()
        finally:
            user32.DestroyWindow(owner)

    def paste_picture(hwnd, png, rgba, width, height, message, enter, delay):
        _copy_picture(png, rgba, width, height)
        _wait_for_keys_up()
        _focus(hwnd)
        time.sleep(0.15)
        _press(VK_V, VK_CONTROL)
        if message:
            time.sleep(0.3)
            _type(message)
        if enter:
            # The chat needs a moment to attach the picture before it will send.
            time.sleep(delay)
            _press(VK_RETURN)

    class Hotkeys:
        """Registers the global shortcuts on a thread of their own and calls on_press(action)."""

        def __init__(self, on_press):
            self.on_press = on_press
            self.wanted, self.status = {}, {}
            self.applied = threading.Event()
            started = threading.Event()
            threading.Thread(target=self._run, args=(started,), daemon=True).start()
            started.wait(2)

        def apply(self, shortcuts):
            """Returns {action: 'global' | 'app' | 'taken'}."""
            self.wanted = dict(shortcuts)
            self.applied.clear()
            user32.PostThreadMessageW(self.thread, WM_APP, 0, 0)
            self.applied.wait(2)
            return self.status

        def _run(self, started):
            self.thread = kernel32.GetCurrentThreadId()
            message = wintypes.MSG()
            user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)  # Creates this thread's message queue.
            started.set()
            actions = []
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY and message.wParam < len(actions):
                    self.on_press(actions[message.wParam])
                elif message.message == WM_APP:
                    for hotkey_id in range(len(actions)):
                        user32.UnregisterHotKey(None, hotkey_id)
                    actions, status = [], {}
                    for action, combo in self.wanted.items():
                        if not combo or not is_global(combo):
                            status[action] = "app"
                            continue
                        key, mods = parse_shortcut(combo)
                        flags = sum(MOD_KEYS.get(m, 0) for m in mods) | MOD_NOREPEAT
                        vk = VIRTUAL_KEYS.get(key)
                        if vk and user32.RegisterHotKey(None, len(actions), flags, vk):
                            actions.append(action)
                            status[action] = "global"
                        else:
                            status[action] = "taken"
                    self.status = status
                    self.applied.set()

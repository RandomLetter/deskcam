"""Opens the camera window and saves its photos and videos to the folders in settings.json."""
import json
import os
import queue
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import desktop

MAC = sys.platform == "darwin"
PORT = 8765
ORIGIN = f"http://127.0.0.1:{PORT}"
# Inside the packaged Mac app the page sits in the bundle, not next to this file.
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
PAGE = HERE / "camera.html"
# The Mac app bundle is read-only, so its settings and browser profile live in the user's Library.
DATA = Path.home() / "Library" / "Application Support" / "DeskCam" if MAC else HERE
SETTINGS = DATA / "settings.json"
PROFILE = DATA / "Browser" if MAC else Path.home() / ".camera-app-browser"
CAPTURES = Path.home() / "Camera Captures"
MIN_VIDEO_BYTES = 4096  # Smaller than this is a file header with no frames.
CLOSE_GRACE = 3  # Seconds to wait after the page closes, in case it was only reloaded.
if MAC:
    BROWSERS = [
        f"{apps}/{name}.app/Contents/MacOS/{name}"
        for name in ("Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium")
        for apps in ("/Applications", str(Path.home() / "Applications"))
    ]
else:
    BROWSERS = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
DEFAULTS = {
    "photoFolder": str(CAPTURES),
    "videoFolder": str(CAPTURES),
    "resolution": "4096x3072",
    "videoShape": "full",
    "photoFormat": "jpg",
    "sound": True,
    "sendTarget": "last",
    "sendSize": 2048,
    "sendMessage": "",
    "sendEnter": True,
    "sendDelay": 2.0,  # Seconds the chat gets to attach the picture before Enter.
    "shortcuts": {"snap": "Space", "record": "KeyR", "send": "Ctrl+Alt+KeyS"},
}
SEND_TARGETS = {"last", "claude", "chatgpt"}
SEND_SIZES = {0, 1568, 2048, 3072}
FOLDER_PICKER = """
import sys, tkinter, tkinter.filedialog
sys.stdout.reconfigure(encoding="utf-8")
root = tkinter.Tk()
root.withdraw()
root.attributes("-topmost", True)
print(tkinter.filedialog.askdirectory(parent=root, initialdir=sys.argv[1], title="Choose a folder"))
"""
MAC_FOLDER_PICKER = [
    "osascript",
    "-e", "on run argv",
    "-e", "activate",
    "-e", 'POSIX path of (choose folder with prompt "Choose a folder" default location (POSIX file (item 1 of argv)))',
    "-e", "end run",
]

recording = None
shutdown = threading.Event()
closing = None
hotkeys = None
browser_pid = None
shortcut_target = None  # The window in front when the send shortcut was pressed.
listeners = []  # One queue per open page, fed by the global shortcuts.


def load_settings():
    try:
        saved = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return {**DEFAULTS, **saved, "shortcuts": {**DEFAULTS["shortcuts"], **saved.get("shortcuts", {})}}


def check_send_settings(merged):
    shortcuts = merged["shortcuts"]
    if (merged["sendTarget"] not in SEND_TARGETS or merged["sendSize"] not in SEND_SIZES
            or not isinstance(merged["sendMessage"], str) or set(shortcuts) != set(DEFAULTS["shortcuts"])
            or not all(isinstance(combo, str) for combo in shortcuts.values())):
        raise OSError("Those send settings aren't valid.")
    used = [combo for combo in shortcuts.values() if combo]
    if len(used) != len(set(used)):
        raise OSError("Two actions can't share the same shortcut.")


def save_settings(changes):
    merged = {**load_settings(), **{k: v for k, v in changes.items() if k in DEFAULTS}}
    check_send_settings(merged)
    for key in ("photoFolder", "videoFolder"):
        folder = Path(merged[key]).expanduser()
        if not folder.is_absolute():
            example = "/Users/you/Pictures" if MAC else "C:\\Users\\you\\Pictures"
            raise OSError(f"Folders need a full path, such as {example}")
        folder.mkdir(parents=True, exist_ok=True)
        merged[key] = str(folder)
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def choose_folder(start):
    """Shows the system folder picker; returns '' if it is cancelled."""
    if MAC:
        # The picker can only start in a folder that exists.
        begin = Path(start).expanduser() if start else Path.home()
        while not begin.is_dir() and begin != begin.parent:
            begin = begin.parent
        result = subprocess.run([*MAC_FOLDER_PICKER, str(begin)], capture_output=True, encoding="utf-8")
    else:
        result = subprocess.run(
            [sys.executable, "-c", FOLDER_PICKER, start],
            capture_output=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW,
        )
    chosen = result.stdout.strip()
    return str(Path(chosen)) if chosen else ""


def open_folder(folder):
    if MAC:
        subprocess.run(["open", folder])
    else:
        os.startfile(folder)


def new_path(setting, kind, ext):
    folder = Path(load_settings()[setting])
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S-%f")[:-3]
    return folder / f"{kind} {stamp}.{ext}"


def settings_reply(settings):
    return {**settings, "shortcutStatus": hotkeys.status}


def on_shortcut(action):
    """Runs on the shortcut thread: tells the page what to do, which then does it like a button press."""
    global shortcut_target
    if action == "send":
        shortcut_target = desktop.foreground()
    # Only the newest page acts, so a leftover window can't send a second copy.
    if listeners:
        listeners[-1].put(action)


def send_to_chat(query, body):
    settings = load_settings()
    width, height, png_size = (int(query[k][0]) for k in ("w", "h", "png"))
    png, rgba = body[:png_size], body[png_size:]
    if len(rgba) != width * height * 4:
        raise OSError("The picture didn't arrive in one piece.")
    preferred = shortcut_target if query.get("shortcut") == ["1"] else None
    where, name = desktop.find_chat(settings["sendTarget"], preferred, {browser_pid, os.getpid()})
    desktop.paste_picture(where, png, rgba, width, height,
                          settings["sendMessage"], settings["sendEnter"], settings["sendDelay"])
    return name


def page_opened():
    if closing:
        closing.cancel()


def page_closed():
    """Quits once the page has stayed closed; on a Mac, closing the window leaves the browser running."""
    global closing
    page_opened()
    closing = threading.Timer(CLOSE_GRACE, quit_if_unused)
    closing.start()


def quit_if_unused():
    # An open page keeps its shortcut stream connected, even when its window lives in another browser process.
    if not listeners:
        shutdown.set()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def reply_json(self, **fields):
        self.reply(200, json.dumps(fields).encode())

    def do_GET(self):
        path = urlparse(self.path).path
        page_opened()
        if path == "/":
            self.reply(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/settings":
            self.reply_json(**settings_reply(load_settings()))
        elif path == "/events":
            self.stream_shortcuts()
        else:
            self.reply(404, b"Not found", "text/plain")

    def stream_shortcuts(self):
        """Server-sent events: one line per global shortcut press, while the page stays open."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        mine = queue.Queue()
        listeners.append(mine)
        try:
            while not shutdown.is_set():
                try:
                    self.wfile.write(f"data: {mine.get(timeout=5)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": still here\n\n")
        except OSError:
            pass  # The page closed or reloaded.
        finally:
            listeners.remove(mine)
            if not listeners:
                page_closed()  # Covers a browser that crashed or was killed without saying goodbye.

    def do_POST(self):
        url = urlparse(self.path)
        origin = self.headers.get("Origin")
        # Only this app's own page may write files, not other websites.
        if origin != ORIGIN and not (url.path == "/bye" and origin is None):
            self.reply(403, b"Forbidden", "text/plain")
            return
        query = parse_qs(url.query)
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            if url.path == "/send":
                self.reply_json(name=send_to_chat(query, body))
            else:
                self.handle_action(url.path, query.get("ext", [""])[0], body)
        except OSError as error:
            self.reply(500, str(error).encode(), "text/plain; charset=utf-8")

    def handle_action(self, path, ext, body):
        global recording
        if path == "/still" and ext in ("jpg", "png"):
            target = new_path("photoFolder", "Photo", ext)
            target.write_bytes(body)
            self.reply_json(name=target.name)
        elif path == "/start" and ext in ("mp4", "webm"):
            recording = new_path("videoFolder", "Video", ext)
            recording.touch()
            self.reply_json(name=recording.name)
        elif path == "/chunk" and recording:
            with recording.open("ab") as f:
                f.write(body)
            self.reply_json()
        elif path == "/stop" and recording:
            finished, recording = recording, None
            if finished.stat().st_size < MIN_VIDEO_BYTES:
                finished.unlink()  # A recording that failed to start has no video to keep.
            self.reply_json(name=finished.name)
        elif path == "/settings":
            saved = save_settings(json.loads(body))
            hotkeys.apply(saved["shortcuts"])
            self.reply_json(**settings_reply(saved))
        elif path == "/pause-shortcuts":
            # While a shortcut box is being set, its keys must reach the page.
            hotkeys.apply({})
            self.reply_json()
        elif path == "/resume-shortcuts":
            hotkeys.apply(load_settings()["shortcuts"])
            self.reply_json(**settings_reply(load_settings()))
        elif path == "/choose-folder":
            self.reply_json(path=choose_folder(body.decode()))
        elif path == "/folder":
            settings = load_settings()
            for folder in {settings["photoFolder"], settings["videoFolder"]}:
                Path(folder).mkdir(parents=True, exist_ok=True)
                open_folder(folder)
            self.reply_json()
        elif path == "/bye":
            page_closed()
            self.reply_json()
        else:
            self.reply(400, b"Bad request", "text/plain")


def open_window():
    """Returns the running browser window, or None if there is no browser to wait for."""
    browser = next((b for b in BROWSERS if Path(b).exists()), None)
    if not browser:
        webbrowser.open(ORIGIN)
        return None
    flags = [
        f"--app={ORIGIN}",
        f"--user-data-dir={PROFILE}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1280,860",
        # Keeps the camera live while the chat window is in front, for shortcut presses from there.
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-background-timer-throttling",
    ]
    if not MAC:
        # The camera's 13MP mode only starts through DirectShow, not Chrome's default.
        flags.append("--disable-features=MediaFoundationVideoCapture")
    # Older macOS passes a -psn_ argument to apps it launches.
    flags += [arg for arg in sys.argv[1:] if not arg.startswith("-psn_")]
    return subprocess.Popen([browser, *flags])


def main():
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        open_window()  # Already running: this brings its window forward.
        return
    global hotkeys, browser_pid
    hotkeys = desktop.Hotkeys(on_shortcut)
    hotkeys.apply(load_settings()["shortcuts"])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # DESKCAM_HEADLESS runs only the server, for the build's smoke test.
    window = None if os.environ.get("DESKCAM_HEADLESS") else open_window()
    if window:
        browser_pid = window.pid
        # If the profile's browser was already running, ours hands the window to it and exits at once.
        threading.Thread(target=lambda: (window.wait(), page_closed()), daemon=True).start()
    shutdown.wait()
    if window and window.poll() is None:
        window.terminate()


if __name__ == "__main__":
    main()

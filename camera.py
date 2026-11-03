"""Opens the camera window and saves its photos and videos to the folders in settings.json."""
import json
import os
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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
}
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


def load_settings():
    try:
        saved = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return {**DEFAULTS, **saved}


def save_settings(changes):
    merged = {**load_settings(), **{k: v for k, v in changes.items() if k in DEFAULTS}}
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


def page_opened():
    if closing:
        closing.cancel()


def page_closed():
    """Quits once the page has stayed closed; on a Mac, closing the window leaves the browser running."""
    global closing
    page_opened()
    closing = threading.Timer(CLOSE_GRACE, shutdown.set)
    closing.start()


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
            self.reply_json(**load_settings())
        else:
            self.reply(404, b"Not found", "text/plain")

    def do_POST(self):
        url = urlparse(self.path)
        origin = self.headers.get("Origin")
        # Only this app's own page may write files, not other websites.
        if origin != ORIGIN and not (url.path == "/bye" and origin is None):
            self.reply(403, b"Forbidden", "text/plain")
            return
        ext = parse_qs(url.query).get("ext", [""])[0]
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            self.handle_action(url.path, ext, body)
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
            self.reply_json(**save_settings(json.loads(body)))
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
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # DESKCAM_HEADLESS runs only the server, for the build's smoke test.
    window = None if os.environ.get("DESKCAM_HEADLESS") else open_window()
    if window:
        threading.Thread(target=lambda: (window.wait(), shutdown.set()), daemon=True).start()
    shutdown.wait()
    if window and window.poll() is None:
        window.terminate()


if __name__ == "__main__":
    main()

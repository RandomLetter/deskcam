# PyInstaller recipe for the Mac app. The build workflow runs it on an Apple Silicon Mac:
#   pyinstaller --noconfirm mac/DeskCam.spec
import os
from pathlib import Path

root = Path(SPECPATH).parent
version = os.environ.get("DESKCAM_VERSION", "1.0.0")

a = Analysis([str(root / "camera.py")], datas=[(str(root / "camera.html"), ".")])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="DeskCam", console=False, target_arch="arm64")
coll = COLLECT(exe, a.binaries, a.datas, name="DeskCam")
app = BUNDLE(
    coll,
    name="DeskCam.app",
    bundle_identifier="io.github.deskcam",
    version=version,
    info_plist={
        "CFBundleShortVersionString": version,
        "LSMinimumSystemVersion": "12.0",
        # No Dock icon of its own: the camera window is the app, and closing it quits.
        "LSUIElement": True,
        # The browser window runs under DeskCam, so macOS asks on DeskCam's behalf.
        "NSCameraUsageDescription": "DeskCam shows and records your document camera.",
        "NSMicrophoneUsageDescription": "DeskCam records sound from your document camera's microphone.",
        "NSDesktopFolderUsageDescription": "DeskCam saves photos and videos to the folder you choose.",
        "NSDocumentsFolderUsageDescription": "DeskCam saves photos and videos to the folder you choose.",
        "NSDownloadsFolderUsageDescription": "DeskCam saves photos and videos to the folder you choose.",
    },
)

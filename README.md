# DeskCam

A small app for viewing an IPEVO VZ-R Ultra document camera (or any webcam), taking photos and recording video. Runs on Windows and on Apple Silicon Macs.

- **Snap** (Space) saves a photo, **Record** (R) starts and stops a video (MP4).
- The ⚙ button sets the save folders, resolution (up to 13MP, 4096×3072), video shape, photo format and sound.
- The top-right corner shows the live frame rate.

Photos and videos go to `Camera Captures` in your home folder unless you choose other folders.

The app is a small Python server (`camera.py`) that opens `camera.html` in a Chrome or Edge app window. Closing the window quits the app.

## Mac

Install:

1. Download `DeskCam.dmg` from the latest **Build Mac app** run under the repo's Actions tab (or a release, if there is one).
2. Open it and drag **DeskCam** into **Applications**.
3. The first time, open DeskCam from Applications. macOS will say it can't verify the app, because it isn't signed with an Apple developer certificate. Click **Done**, then go to **System Settings → Privacy & Security**, scroll down and click **Open Anyway** next to DeskCam.
4. Allow camera and microphone access when macOS asks.

Needs Google Chrome, Microsoft Edge or Brave installed. Without one of those it opens in your default browser instead and keeps running until that tab is closed.

Settings are stored in `~/Library/Application Support/DeskCam`.

Build: every push runs `.github/workflows/build-mac.yml` on an Apple Silicon runner, which packages the app with PyInstaller (`mac/DeskCam.spec`) and makes the DMG. Pushing a tag like `v1.0.0` also attaches the DMG to a GitHub release.

## Windows

Needs Python 3 and Chrome or Edge. Double-click `Open Camera.bat`. Settings are stored in `settings.json` next to the app.

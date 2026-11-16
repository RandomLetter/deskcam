# DeskCam

A small app for viewing an IPEVO VZ-R Ultra document camera (or any webcam), taking photos and recording video. Runs on Windows and on Apple Silicon Macs.

- **Snap** (Space) saves a photo, **Record** (R) starts and stops a video (MP4).
- **Send to chat** (Ctrl+Alt+S) pastes the camera's picture into your open Claude or ChatGPT conversation and presses Enter, for reading a book page by page with an AI. It works with the desktop apps and browser tabs: it copies the picture, brings the chat window to the front and pastes, as you would by hand.
- The ⚙ button sets the save folders, resolution (up to 13MP, 4096×3072), video shape, photo format, sound, where and how pictures are sent, and the keyboard shortcuts.
- The top-right corner shows the live frame rate.

### Send to chat

- **Send to:** the window you used last (default), or the first Claude or ChatGPT window.
- **Picture size:** 2048 px on the long side by default, which is plenty for reading a page.
- **Message:** optional text typed along with each picture, such as "Here's the next page".
- **Press Enter:** on by default. DeskCam waits 2 seconds after pasting so the chat can attach the picture first; turn it off to add your own question before sending.

The picture goes through the clipboard, so it replaces whatever you had copied.

### Shortcuts

Set them in ⚙ → Keyboard shortcuts. On Windows, a shortcut that uses Ctrl, Alt or an F key also works while another app is in front, so you can stay in your chat and press it after turning each page. Shortcuts without those keys work only while DeskCam is in front. On a Mac, all shortcuts work only while DeskCam is in front for now.

Photos and videos go to `Camera Captures` in your home folder unless you choose other folders.

The app is a small Python server (`camera.py`) that opens `camera.html` in a Chrome or Edge app window. Closing the window quits the app.

## Mac

Install:

1. Download `DeskCam.dmg` from the latest **Build Mac app** run under the repo's Actions tab (or a release, if there is one).
2. Open it and drag **DeskCam** into **Applications**.
3. The first time, open DeskCam from Applications. macOS will say it can't verify the app, because it isn't signed with an Apple developer certificate. Click **Done**, then go to **System Settings → Privacy & Security**, scroll down and click **Open Anyway** next to DeskCam.
4. Allow camera and microphone access when macOS asks.
5. For **Send to chat**, macOS will also ask to let DeskCam control your computer. Turn DeskCam on under **System Settings → Privacy & Security → Accessibility**; it needs this to paste into the chat app.

Needs Google Chrome, Microsoft Edge or Brave installed. Without one of those it opens in your default browser instead and keeps running until that tab is closed.

Settings are stored in `~/Library/Application Support/DeskCam`.

Build: every push runs `.github/workflows/build-mac.yml` on an Apple Silicon runner, which packages the app with PyInstaller (`mac/DeskCam.spec`) and makes the DMG. Pushing a tag like `v1.0.0` also attaches the DMG to a GitHub release.

## Windows

Needs Python 3 and Chrome or Edge. Double-click `Open Camera.bat`. Settings are stored in `settings.json` next to the app.

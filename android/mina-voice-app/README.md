# Mina Voice Android App (MVP)

Android app for voice-driving Mina from your phone.

## Build Environment

You do not need a separate repository or separate Python environment for this app.
You do need Android build tooling on your machine:

- Android Studio (recommended) or command-line Android SDK tools
- JDK 17
- Android SDK + Build Tools (installed via Android Studio SDK Manager)

This app lives in the same Mina repo and talks to your existing MK1 API.

If you prefer command-line builds, generate Gradle wrapper once after installing tooling:

- `gradle wrapper`
- `./gradlew assembleDebug` (Windows: `gradlew.bat assembleDebug`)

## What it does

- Continuous speech listening (toggle start/stop)
- Optional wake-word gate (`Hey Mina`)
- Sends command text to MK1 API `/process`
- Speaks Mina reply on phone via Android TTS

## Notes

- This MVP routes Spotify control through Mina/MK1 server (existing `spotify_link` tool).
- Spotify Android SDK is not required for this first version.
- If you want direct Spotify App Remote control in-app, we can add that next.

## Setup

1. Open this folder in Android Studio:
   - `android/mina-voice-app`
2. Let Gradle sync.
3. Connect Android phone and run app.
4. In app, set MK1 API URL.
   - Tailscale (recommended): `http://your-node-name:8000`
   - Tailscale HTTPS (if enabled): `https://your-node-name.your-tailnet.ts.net`
   - LAN fallback: `http://192.168.1.100:8000`
5. Tap `Start Listening` and grant microphone permission.

## Typical command

Say:
- `Hey Mina play spotify`
- `Hey Mina pause spotify`
- `Hey Mina play a track from my favorites library`

## Network

The app allows cleartext HTTP for local-network and Tailscale HTTP calls to MK1 API.

### Tailscale quick setup

1. Make sure phone and MK1 machine are both connected to the same tailnet.
2. Start MK1 API listening on a reachable interface (not localhost-only):
   - `python launch_api.py --host 0.0.0.0 --port 8000`
3. On phone app, tap `Check API` after entering your Tailscale URL.
4. If reachable, tap `Save API`, then use `Start Listening`.

If you keep localhost-only (`127.0.0.1`), phone clients cannot reach the API over Tailscale.

## Publish To Dedicated GitHub Repo

If your Android repo is empty (for example `DogFoodDayZ/AI-friendly-Spotify-build-for-android`),
you can publish this app as a standalone repo using:


This prepares a clean export in `.tmp_publish_android_repo` with only Android app files.

To push to GitHub:


Safety defaults:

- Secret-like files are excluded automatically (`.env`, `.env.local`, `google-services.json`, key/cert files).
- A secret pattern scan runs before commit/push and aborts on potential matches.
- Only use `-SkipSecretScan` if you reviewed findings and confirmed false positives.

Optional overrides:

- `-RepoUrl https://github.com/<owner>/<repo>.git`
- `-Branch main`
- `-SourceDir android/mina-voice-app`

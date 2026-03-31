# Pranathi Employee macOS App

This is a native SwiftUI macOS client for the Pranathi employee attendance flow. It uses the same backend endpoints as the previous mobile client:

- `POST /api/mobile/signup`
- `POST /api/mobile/login`
- `GET /api/mobile/attendance/history`
- `POST /api/mobile/attendance/punch`
- `POST /api/mobile/logout`

## Features

- Sign up with `contoso.com` or `litware.com`
- Log in and persist the backend URL and session locally
- Punch in and punch out
- Refresh recent attendance history
- Log out

## Open In Xcode

1. Open Xcode.
2. Choose `File -> Open`.
3. Open `desktop/pranathi-employee-macos/Package.swift`.
4. Let Xcode resolve the package.
5. Run the `PranathiEmployeeMacOS` target.

## Backend URL

You can point the app at either:

- deployed dev staging: `https://myappdev8017f-staging.azurewebsites.net`
- a local FastAPI server on your Mac, for example: `http://127.0.0.1:8000`

## Notes

- This app is intentionally lightweight and does not require Android Studio or the Android SDK.
- Session state is stored with `UserDefaults` on the local Mac.

# Pranathi Employee Android App

This Android app replaces employee login on the website. Employees can:

- Sign up with a `contoso.com` or `litware.com` email
- Log in against the FastAPI mobile endpoints
- Punch in and punch out
- Review recent attendance history

The app talks to these backend routes:

- `POST /api/mobile/signup`
- `POST /api/mobile/login`
- `GET /api/mobile/me`
- `GET /api/mobile/attendance/history`
- `POST /api/mobile/attendance/punch`
- `POST /api/mobile/logout`

## Build

1. Open `mobile/pranathi-employee-android` in Android Studio.
2. Let Android Studio install the required Android SDK and Gradle distribution.
3. Build the debug APK with `Build > Build Bundle(s) / APK(s) > Build APK(s)`.

Expected output:

- `app/build/outputs/apk/debug/app-debug.apk`

## Note

This repository environment does not currently have Android SDK tools installed, so the APK could not be generated directly here.

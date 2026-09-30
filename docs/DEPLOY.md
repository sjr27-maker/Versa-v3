# Deploying Versa for the demo

The shape: **one server on Google Cloud Run** (API + database + sign-in), and
**the Android app** installed on invited people's phones, talking to it. Versa
is invite-only: an account can only be created with an invite code.

```
phone (Versa APK) ──https/wss──▶ Cloud Run "versa" ──▶ Cloud SQL (Postgres 16 + pgvector)
       │                              │  ├─ Secret Manager (Gemini, session secret, RevenueCat)
       └── Google / email sign-in ◀── Firebase Auth (tokens checked by the server)
```

Everything below is done once. Steps marked **(you)** need a browser and your
Google account; the rest is a script.

---

## 1. Firebase: Google + email sign-in (you, ~15 min)

1. <https://console.firebase.google.com> → **Add project** → pick your GCP
   project (the same one Cloud Run will use), Analytics off.
2. **Build → Authentication → Get started → Sign-in method**: enable
   **Google** and **Email/Password**.
3. **Authentication → Settings → Authorized domains**: add your Cloud Run
   domain once you have it (step 3), e.g. `versa-xxxx-el.a.run.app`.
4. **Project settings → Your apps**:
   - **Add app → Web** (nickname "versa-web"). Copy `apiKey`, `appId`,
     `messagingSenderId`, `authDomain`, `projectId`.
   - **Add app → Android**, package name **`dev.versa.versa_app`**. Skip the
     `google-services.json` download steps (the app is configured through
     `config/firebase.json` instead) — but open that file once to copy the
     Android `appId` (`mobilesdk_app_id`) and `api_key.current_key`.
5. `copy app\config\firebase.example.json app\config\firebase.json` and fill
   it in. None of these values are secret (Firebase client config is public;
   the server checks every token), so it is fine to commit.

The **SHA fingerprints** for the Android app come in step 4 (the APK build
prints them): add both SHA-1 and SHA-256 under the Android app in Project
settings. Google sign-in on Android fails without them.

## 2. Try it locally first

```powershell
# .env: add FIREBASE_PROJECT_ID=<your project id>
uv run versa migrate
uv run versa invite create --count 3          # prints codes + links
uv run versa serve
cd app; flutter run -d edge --dart-define-from-file=config/firebase.json
```

On a laptop the server allows the two testers by name (`sooraj`, `adithya`)
with no code; Google/email work once `FIREBASE_PROJECT_ID` is set and
`localhost` is an authorized domain (it is by default).

## 3. The server on Cloud Run

Needs `gcloud` signed in (`gcloud auth login`) and billing on the project.
`GEMINI_API_KEY` (and, for purchases, the RevenueCat keys) are read from
`.env`.

```powershell
.\scripts\deploy_gcp.ps1 -Project <gcp-project-id> -FirebaseProject <firebase-project-id>
```

It creates (only what's missing): the Cloud SQL instance + database, the
secrets (a random session secret and a **tester code** it prints once — keep
it), builds the image with Cloud Build, runs the migrations as a job, and
deploys the service with sign-in on and invites required. It prints the URL.

Settings worth knowing (all in the script): **one always-on instance**
(`--min-instances=1 --max-instances=1`) because live chats and study rooms keep
state in memory, a **1-hour timeout** so chat WebSockets aren't cut, and **no
CPU throttling** because answers keep being written after a reply is sent.

Re-deploy after a change: run the same command again (a new image, the
migrations, a new revision).

## 4. The Android app

No Android Studio needed — the build runs in Docker:

```powershell
.\scripts\build_apk.ps1 -Api https://versa-xxxx-el.a.run.app
```

The first run makes the app's signing key (`app/android/versa-release.jks` +
`key.properties`, git-ignored — **back them up**) and prints its SHA-1/SHA-256:
add both in Firebase (step 1). The APK lands at
`app/build/app/outputs/flutter-apk/app-release.apk`.

For RevenueCat purchases in the app, `config/firebase.json` can also carry
`RC_TEST_KEY` (Test Store) or `RC_GOOGLE_KEY` (Play) — see `app/README.md`.

**Demo builds (Test Store).** RevenueCat's SDK crashes on purpose in any
non-debuggable Android build that carries a Test Store key. So when
`RC_TEST_KEY` is set, `build_apk.ps1` builds a *demo* APK in Flutter's
**profile** mode: compiled ahead of time (near-release speed) and debuggable
by design, signed with the release key (`android/app/build.gradle.kts`), and
saved as the same `app-release.apk`. (A debuggable *release* build doesn't
work: Flutter then compiles the Dart code in slow debug mode.) Purchases are simulated, show in the
RevenueCat dashboard as sandbox data, and should still reach the server's
webhook (check once in RevenueCat → Integrations → Webhooks). Play refuses debuggable uploads, so a demo build can't ship.

**Play builds.** `build_apk.ps1 -Api <url> -Bundle` makes an `.aab` for
Play internal testing; it refuses while `RC_TEST_KEY` is set (use
`RC_GOOGLE_KEY=goog_...`). Play re-signs the app, so add Play's app signing
key SHA-1/SHA-256 (Play Console → App integrity) to Firebase as well.

## 5. Invites

Make codes against the **deployed** database through the Cloud SQL proxy:

```powershell
# terminal 1
cloud-sql-proxy <project>:asia-south1:versa-db --port 5439
# terminal 2 -- the password is in the versa-database-url secret
$env:DATABASE_URL = "postgresql://versa:<password>@127.0.0.1:5439/versa"
$env:VERSA_PUBLIC_URL = "https://versa-xxxx-el.a.run.app"
uv run versa invite create --count 10 --note "judges"
uv run versa invite create --uses 25 --note "class demo" --days 7
uv run versa invite list
uv run versa invite revoke ABCD2345 --reason "posted publicly"
```

Each code prints with its link, `https://…/invite/CODE`: a small page with the
code and a "Get Versa for Android" button (set `VERSA_ANDROID_URL` on the
service, or pass `-AndroidUrl` to the deploy script, to where the APK lives).

**Getting the APK only to invited people.** Two good options:

- **Firebase App Distribution** (recommended): Firebase console → App
  Distribution → upload the APK → add testers by email. Only those emails can
  download it, and they get update notifications. Put the tester link in
  `VERSA_ANDROID_URL`.
- A link to the APK (e.g. a private Cloud Storage object with a signed URL).
  Anyone with the link can download it, but they still can't make an account
  without an invite code, so the code is the real gate either way.

## 6. Showing it on a laptop screen (the demo)

The demo is the real Android app, not a website. To put the phone on the big
screen:

- **scrcpy** (free, Windows): plug the phone in by USB with USB debugging on,
  run `scrcpy` — the phone's screen appears in a window you can present, and you
  can drive it with the mouse. `scrcpy --max-size 1080 --stay-awake`.
- Or the **Android Emulator** (Android Studio) running the same APK.

Screenshots for store listings or a hackathon form: take them **on the phone**
(power + volume down) or `adb exec-out screencap -p > shot.png` — these have no
device frame. A 1024×1024 app icon needs to be made separately (the app still
uses Flutter's default icon; `flutter_launcher_icons` can generate all sizes
from one PNG).

## 7. Before real users

- **Store release.** A first Google Play release from a new personal developer
  account needs a closed test with 12+ testers for 14 days before production —
  plan for that if a store release date matters. iOS needs an Apple developer
  account and TestFlight.
- **Children's data.** Under-18s tick a parent/guardian box; that is a record
  of consent, not a *verifiable* one as India's DPDP Act asks for. Real
  verification (e.g. a parent's email confirmation) is still to build —
  see docs/IDEAS.md.
- **Signing out everywhere.** Session tokens are signed, not stored: rotating
  `versa-session-secret` signs everyone out. There is no per-device revoke yet.
- Study rooms still identify members by name inside a room (only signed-in
  people can reach them).

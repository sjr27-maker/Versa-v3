<#
.SYNOPSIS
  Build the Versa Android app (a release APK) -- no Android Studio needed: the
  build runs in Docker, in the same Flutter image the server build uses.

.DESCRIPTION
  The first run makes a release signing key (app/android/versa-release.jks +
  app/android/key.properties, both git-ignored -- back them up; lose them and
  every tester has to uninstall and reinstall) and prints its SHA-1 / SHA-256.
  Add BOTH to Firebase (Project settings -> Your apps -> Android app ->
  Add fingerprint): Google sign-in on Android only works for a registered key.

  Output: app/build/app/outputs/flutter-apk/app-release.apk -- share it through
  Firebase App Distribution (invite testers by email) or a link on the invite
  page (VERSA_ANDROID_URL). See docs/DEPLOY.md.

  Demo builds: when app/config/firebase.json has an RC_TEST_KEY (RevenueCat's
  Test Store), the APK is a Flutter PROFILE build -- compiled ahead of time
  like release (full speed), but debuggable, which the Test Store SDK
  requires (it crashes on purpose otherwise). Signed with the release key
  (android/app/build.gradle.kts), saved under the same app-release.apk name.
  Such a build can't go to Play; -Bundle refuses while the test key is set.

  -Bundle builds an .aab for Google Play instead (needs RC_GOOGLE_KEY, not
  RC_TEST_KEY): app/build/app/outputs/bundle/release/app-release.aab.

.EXAMPLE
  .\scripts\build_apk.ps1 -Api https://versa-xxxxxxxx-el.a.run.app
#>
param(
    [Parameter(Mandatory = $true)][string]$Api,
    [string]$Image = 'versa-flutter:local',
    [switch]$Bundle
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path $PSScriptRoot -Parent) -ErrorAction Stop
function Step($msg) { Write-Host "versa: $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "versa: $msg" -ForegroundColor Red; exit 1 }

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail 'Docker is not installed' }
if (-not (Test-Path app/config/firebase.json)) {
    Fail 'app/config/firebase.json is missing -- copy app/config/firebase.example.json and fill it in (docs/DEPLOY.md)'
}
$app = (Resolve-Path app).Path

$config = Get-Content app/config/firebase.json -Raw | ConvertFrom-Json
$testKey = [string]$config.RC_TEST_KEY
if ($testKey -eq 'test_...') {
    Fail 'RC_TEST_KEY in app/config/firebase.json is still the example placeholder -- put the real Test Store key there, or delete the line'
}
$demo = $testKey -ne ''
if ($Bundle -and $demo) {
    Fail 'a Play build must not carry the Test Store key: remove RC_TEST_KEY from app/config/firebase.json and set RC_GOOGLE_KEY (goog_...)'
}
if ($Bundle -and -not [string]$config.RC_GOOGLE_KEY) {
    Write-Host '  no RC_GOOGLE_KEY in app/config/firebase.json: this Play build will have no purchases' -ForegroundColor Yellow
}
if ($demo) {
    Write-Host '  RC_TEST_KEY is set: DEMO build (profile mode, RevenueCat Test Store purchases, never for Play)' -ForegroundColor Yellow
}
$target = if ($Bundle) { 'appbundle' } else { 'apk' }
$mode = if ($demo) { 'profile' } else { 'release' }

# The Flutter SDK pinned in the Dockerfile's `flutter` stage (the same one the
# server's web build uses). Cached after the first build.
Step 'preparing the pinned Flutter SDK image (first run downloads a few GB)'
docker build --target flutter -t $Image .
if ($LASTEXITCODE -ne 0) { Fail 'could not build the Flutter image' }

if (-not (Test-Path app/android/key.properties)) {
    Step 'making the release signing key (first run only)'
    $b = New-Object byte[] 18
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    $pass = [Convert]::ToBase64String($b).Replace('+', 'x').Replace('/', 'y').TrimEnd('=')
    docker run --rm -v "${app}:/src/app" -w /src/app/android $Image `
        keytool -genkeypair -v -keystore versa-release.jks -storetype JKS -keyalg RSA -keysize 2048 `
        -validity 10000 -alias versa -storepass $pass -keypass $pass -dname "CN=Versa, O=Versa, C=IN"
    if ($LASTEXITCODE -ne 0) { Fail 'keytool failed' }
    @"
storeFile=versa-release.jks
storePassword=$pass
keyAlias=versa
keyPassword=$pass
"@ | Set-Content -Encoding ascii app/android/key.properties
}

Step 'the key''s fingerprints (add both to Firebase if you haven''t)'
$props = @{}
Get-Content app/android/key.properties | ForEach-Object { $k, $v = $_ -split '=', 2; $props[$k] = $v }
docker run --rm -v "${app}:/src/app" -w /src/app/android $Image `
    keytool -list -v -keystore $props['storeFile'] -alias $props['keyAlias'] -storepass $props['storePassword'] |
    Select-String -Pattern 'SHA1:|SHA256:'

Step "building the $target against $Api (first run downloads Gradle and the Android SDK parts: slow)"
# Docker Desktop's network drops lookups under Gradle's burst of parallel
# downloads ("Temporary failure in name resolution"), so: public DNS, Gradle
# retrying each download with backoff (set in the cache volume's own
# gradle.properties, not the repo's), and up to 3 attempts -- every attempt
# keeps what it downloaded, so a retry only fetches what's still missing.
# Also lean on memory: the Gradle user home's gradle.properties outranks the
# project's (-Xmx8G there is for a dev machine), and fewer workers means
# fewer parallel downloads for Docker's network to drop.
$gradleProps = 'org.gradle.jvmargs=-Xmx3G -XX:MaxMetaspaceSize=1G\n' +
               'kotlin.daemon.jvmargs=-Xmx1G\n' +
               'org.gradle.workers.max=2\n' +
               'systemProp.org.gradle.internal.repository.max.retries=8\n' +
               'systemProp.org.gradle.internal.repository.initial.backoff=2000\n' +
               'systemProp.org.gradle.internal.http.connectionTimeout=60000\n' +
               'systemProp.org.gradle.internal.http.socketTimeout=60000\n'
$built = $false
for ($attempt = 1; $attempt -le 3; $attempt++) {
    # versa-android-sdk keeps SDK platforms Gradle installs, so a retry or the
    # next build doesn't download them again (a new volume starts as a copy
    # of the image's SDK).
    docker run --rm --dns 8.8.8.8 --dns 1.1.1.1 -v "${app}:/src/app" -w /src/app -v versa-gradle-cache:/root/.gradle -v versa-android-sdk:/opt/android-sdk-linux $Image sh -c `
        "printf '$gradleProps' > /root/.gradle/gradle.properties && flutter pub get && flutter build $target --$mode --dart-define-from-file=config/firebase.json --dart-define=VERSA_API=$Api"
    $built = $LASTEXITCODE -eq 0
    if ($built) { break }
    if ($attempt -lt 3) { Step "attempt $attempt failed -- retrying (downloads so far are kept)" }
}
if (-not $built) { Fail 'the build failed' }
if ($demo) {
    # same name either way, so installing and sharing steps never change
    Copy-Item app/build/app/outputs/flutter-apk/app-profile.apk app/build/app/outputs/flutter-apk/app-release.apk -Force
}

if ($Bundle) {
    Step 'done: app/build/app/outputs/bundle/release/app-release.aab'
    Write-Host '  upload it in Play Console -> Testing -> Internal testing -> Create new release'
    Write-Host '  then add Play''s app signing key SHA-1/SHA-256 (Setup -> App integrity) to Firebase too'
    exit 0
}
$apk = 'app/build/app/outputs/flutter-apk/app-release.apk'
if ($demo) { Step "done: $apk  (DEMO build, profile mode: RevenueCat Test Store purchases)" } else { Step "done: $apk" }
Write-Host '  install on a phone plugged in by USB:  adb install -r' $apk
Write-Host '  or share it: Firebase App Distribution, or upload it and set VERSA_ANDROID_URL (docs/DEPLOY.md)'

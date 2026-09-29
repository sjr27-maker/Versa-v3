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

.EXAMPLE
  .\scripts\build_apk.ps1 -Api https://versa-xxxxxxxx-el.a.run.app
#>
param(
    [Parameter(Mandatory = $true)][string]$Api,
    [string]$Image = 'versa-flutter:local'
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

Step "building the APK against $Api (first run downloads Gradle and the Android SDK parts: slow)"
docker run --rm -v "${app}:/src/app" -w /src/app -v versa-gradle-cache:/root/.gradle $Image sh -c `
    "flutter pub get && flutter build apk --release --dart-define-from-file=config/firebase.json --dart-define=VERSA_API=$Api"
if ($LASTEXITCODE -ne 0) { Fail 'the build failed' }

$apk = 'app/build/app/outputs/flutter-apk/app-release.apk'
Step "done: $apk"
Write-Host '  install on a phone plugged in by USB:  adb install -r' $apk
Write-Host '  or share it: Firebase App Distribution, or upload it and set VERSA_ANDROID_URL (docs/DEPLOY.md)'

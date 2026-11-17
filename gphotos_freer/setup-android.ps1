<#
  Gets an Android device ready for the one-time sign-in (`gphotos-freer auth`).
  Normally started for you by `gphotos-freer setup` (or the menu you get from plain `gphotos-freer`).

  -Mode phone     Your own Android phone over USB. Downloads only adb (~10 MB).
  -Mode emulator  An emulator on this PC, no Android Studio needed. Downloads ~4 GB, needs Java 17+.

  Both then install ReVanced GmsCore and (if -PhotosApk is given) your patched Google Photos.

  Usage (PowerShell):
    powershell -ExecutionPolicy Bypass -File setup-android.ps1 -Mode emulator -PhotosApk C:\path\to\photos-revanced.apk
    Optional: -SdkDir D:\gphotos-freer-sdk   (default: %LOCALAPPDATA%\gphotos-freer\sdk)
#>
param(
    [ValidateSet("phone", "emulator")]
    [string]$Mode = "emulator",
    [string]$SdkDir = "$env:LOCALAPPDATA\gphotos-freer\sdk",
    [string]$PhotosApk = "",
    [string]$AvdName = "gphotos-uploader"
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # makes Invoke-WebRequest much faster

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

New-Item -ItemType Directory -Force $SdkDir | Out-Null
# Tell gphotos-freer where adb lives.
Set-Content -Path (Join-Path $HOME ".gphotos-freer-sdk") -Value $SdkDir -Encoding utf8
$adb = Join-Path $SdkDir "platform-tools\adb.exe"

if ($Mode -eq "phone") {
    # --- adb only -------------------------------------------------------------------------
    if (-not (Test-Path $adb)) {
        Step "Downloading adb (Android platform-tools, ~10 MB) into $SdkDir"
        $zip = Join-Path $env:TEMP "platform-tools.zip"
        Invoke-WebRequest "https://dl.google.com/android/repository/platform-tools-latest-windows.zip" -OutFile $zip
        Expand-Archive $zip $SdkDir -Force   # the zip holds a platform-tools\ folder
        Remove-Item $zip
    }
    Step "Connect your phone"
    Write-Host "  1. On the phone: Settings -> About phone -> tap 'Build number' 7 times (turns on Developer options)."
    Write-Host "  2. Settings -> System -> Developer options -> turn on 'USB debugging'."
    Write-Host "  3. Plug it into this PC with a USB cable and tap 'Allow' on the phone when it asks."
    Write-Host "Waiting for the phone..."
    & $adb wait-for-device
    Write-Host "Phone connected."
} else {
    # --- Java (sdkmanager needs Java 17+) ---------------------------------------------------
    Step "Checking Java"
    $java = Get-Command java -ErrorAction SilentlyContinue
    if (-not $java) {
        Write-Host "Java 17+ is required for the emulator download. Install it with:" -ForegroundColor Yellow
        Write-Host "  winget install EclipseAdoptium.Temurin.17.JRE"
        Write-Host "then open a new terminal and run setup again."
        exit 1
    }

    # --- Command-line tools ---------------------------------------------------------------
    $tools = Join-Path $SdkDir "cmdline-tools\latest"
    $sdkmanager = Join-Path $tools "bin\sdkmanager.bat"
    $avdmanager = Join-Path $tools "bin\avdmanager.bat"
    if (-not (Test-Path $sdkmanager)) {
        Step "Downloading Android command-line tools into $SdkDir"
        $zip = Join-Path $env:TEMP "android-cmdline-tools.zip"
        Invoke-WebRequest "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip" -OutFile $zip
        $tmp = Join-Path $env:TEMP "android-cmdline-tools"
        if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
        Expand-Archive $zip $tmp
        New-Item -ItemType Directory -Force (Split-Path $tools) | Out-Null
        Move-Item (Join-Path $tmp "cmdline-tools") $tools
        Remove-Item $zip
    }

    # --- SDK packages ---------------------------------------------------------------------
    Step "Accepting Android SDK licenses (answer y to each)"
    & $sdkmanager "--sdk_root=$SdkDir" --licenses

    $image = "system-images;android-36;google_apis_playstore;x86_64"
    Step "Installing adb, the emulator and Android 16 (this is the big download, 3-4 GB)"
    & $sdkmanager "--sdk_root=$SdkDir" "platform-tools" "emulator" $image
    $emulator = Join-Path $SdkDir "emulator\emulator.exe"

    # --- Hardware acceleration ------------------------------------------------------------
    Step "Checking hardware acceleration"
    $accel = & $emulator -accel-check 2>&1 | Out-String
    if ($accel -notmatch "usable") {
        Write-Host $accel
        Write-Host "Hardware acceleration isn't available. Turn on 'Windows Hypervisor Platform':" -ForegroundColor Yellow
        Write-Host "  Start -> 'Turn Windows features on or off' -> tick 'Windows Hypervisor Platform' -> restart."
        exit 1
    }

    # --- The emulator ---------------------------------------------------------------------
    $env:ANDROID_SDK_ROOT = $SdkDir
    $env:ANDROID_AVD_HOME = Join-Path $SdkDir "avd"
    New-Item -ItemType Directory -Force $env:ANDROID_AVD_HOME | Out-Null
    $avdDir = Join-Path $env:ANDROID_AVD_HOME "$AvdName.avd"
    if (-not (Test-Path $avdDir)) {
        Step "Creating the emulator '$AvdName'"
        "no" | & $avdmanager create avd -n $AvdName -k $image -d pixel_6
        $cfg = Join-Path $avdDir "config.ini"
        (Get-Content $cfg) `
            -replace '^disk.dataPartition.size=.*', 'disk.dataPartition.size=8G' `
            -replace '^hw.ramSize=.*', 'hw.ramSize=4096' `
            -replace '^hw.keyboard=.*', 'hw.keyboard=yes' `
            -replace '^hw.gpu.enabled=.*', 'hw.gpu.enabled=yes' | Set-Content $cfg
    }

    Step "Starting the emulator (a window will open)"
    Start-Process $emulator -ArgumentList "-avd", $AvdName, "-no-boot-anim", "-gpu", "auto"
    & $adb wait-for-device
    do { Start-Sleep 3; $booted = (& $adb shell getprop sys.boot_completed 2>$null) } while ("$booted".Trim() -ne "1")
    Write-Host "Booted."
}

# --- GmsCore + patched Photos ---------------------------------------------------------------
$have = & $adb shell pm list packages app.revanced.android 2>$null | Out-String
if ($have -notmatch "app\.revanced\.android\.gms") {
    Step "Installing ReVanced GmsCore"
    $rel = Invoke-RestMethod "https://api.github.com/repos/ReVanced/GmsCore/releases/latest"
    $asset = $rel.assets | Where-Object { $_.name -match '-signed\.apk$' -and $_.name -notmatch 'hw-signed' } | Select-Object -First 1
    $gms = Join-Path $env:TEMP $asset.name
    Invoke-WebRequest $asset.browser_download_url -OutFile $gms
    & $adb install -r $gms
} else {
    Write-Host "ReVanced GmsCore is already installed."
}
& $adb shell dumpsys deviceidle whitelist +app.revanced.android.gms | Out-Null

if ($PhotosApk) {
    Step "Installing your patched Google Photos"
    & $adb install -r $PhotosApk
} elseif ($have -notmatch "app\.revanced\.android\.photos") {
    Write-Host "`nPatched Google Photos isn't installed yet. Install it with:" -ForegroundColor Yellow
    Write-Host "  `"$adb`" install -r C:\path\to\photos-revanced.apk"
}
& $adb shell dumpsys deviceidle whitelist +app.revanced.android.photos 2>$null | Out-Null

Step "Device ready"

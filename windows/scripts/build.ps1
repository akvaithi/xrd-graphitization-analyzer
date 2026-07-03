#!/usr/bin/env pwsh
# Build XRDBridge.dll (release), stage it + the Swift runtime DLLs it needs
# alongside the WinUI app, then build the C# app. The Windows analogue of
# native/scripts/make-app.sh.
#
#   windows/scripts/build.ps1 [-Configuration Debug|Release] [-Msix]
#
# -Msix additionally produces an (unsigned) sideloadable MSIX package under
# windows\XRDAnalyzer\AppPackages\. To install it locally you still need a
# cert whose Subject matches Package.appxmanifest's Publisher (CN=AppPublisher)
# trusted on this machine, one time:
#   $cert = New-SelfSignedCertificate -Type Custom -Subject "CN=AppPublisher" `
#     -KeyUsage DigitalSignature -CertStoreLocation Cert:\CurrentUser\My `
#     -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3","2.5.29.19={text}")
#   Export-Certificate -Cert $cert -FilePath cert.cer
#   Import-Certificate -FilePath cert.cer -CertStoreLocation Cert:\LocalMachine\TrustedPeople  # needs an elevated prompt
# then sign + install each rebuild:
#   signtool sign /fd SHA256 /a /s My /sha1 <thumbprint> <path-to.msix>
#   Add-AppxPackage -Path <path-to.msix>
# signtool.exe ships in the Microsoft.Windows.SDK.BuildTools NuGet package
# already restored for this project (search %USERPROFILE%\.nuget\packages).
param(
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Release",
    [switch]$Msix,
    # Pinned rather than left to auto-detect: the csproj's default RID
    # follows the *invoking process's* architecture, which flips between
    # win-x86/win-x64 depending on which dotnet host got resolved -- not the
    # target machine's architecture. win-x64 covers the overwhelming
    # majority of Windows installs.
    [string]$RuntimeIdentifier = "win-x64"
)
$RepoRoot = Resolve-Path "$PSScriptRoot\..\.."
$Native = Join-Path $RepoRoot "native"
$WindowsDir = Join-Path $RepoRoot "windows"
$AppProject = Join-Path $WindowsDir "XRDAnalyzer\XRDAnalyzer.csproj"

# --- 1. Build the Swift bridge --------------------------------------------
$SwiftConfig = if ($Configuration -eq "Release") { "release" } else { "debug" }
Write-Host "==> swift build -c $SwiftConfig --product XRDBridge"
& swift build -c $SwiftConfig --package-path $Native --product XRDBridge
if ($LASTEXITCODE -ne 0) { throw "swift build failed" }

$BridgeDll = Join-Path $Native ".build\x86_64-unknown-windows-msvc\$SwiftConfig\XRDBridge.dll"
if (-not (Test-Path $BridgeDll)) { throw "XRDBridge.dll not found at $BridgeDll" }

# --- 2. Locate the Swift runtime DLLs (swiftCore.dll, Foundation*.dll, ...) --
# These ship from the "Runtimes" half of the swift.org Windows toolchain
# install (separate from the "Toolchains" half that has the compiler), found
# via PATH rather than a hardcoded version so this survives a toolchain
# upgrade.
$RuntimeDir = $env:PATH -split ";" | Where-Object {
    $_ -and (Test-Path (Join-Path $_ "swiftCore.dll"))
} | Select-Object -First 1
if (-not $RuntimeDir) { throw "Couldn't find swiftCore.dll on PATH - is the Swift Windows runtime installed?" }
Write-Host "==> Swift runtime DLLs: $RuntimeDir"

# --- 3. Stage the redistributable DLLs into the app project -----------------
# Picked up by XRDAnalyzer.csproj's Redist\*.dll item (CopyToOutputDirectory).
$RedistDir = Join-Path $WindowsDir "XRDAnalyzer\Redist"
New-Item -ItemType Directory -Force -Path $RedistDir | Out-Null
Copy-Item $BridgeDll $RedistDir -Force
Copy-Item (Join-Path $RuntimeDir "*.dll") $RedistDir -Force
$DllCount = (Get-ChildItem $RedistDir -Filter "*.dll").Count
Write-Host "==> Staged $DllCount DLLs into $RedistDir"

# --- 4. Build (and optionally package) the C# app ---------------------------
$BuildArgs = @($AppProject, "-c", $Configuration, "-r", $RuntimeIdentifier)
if ($Msix) {
    $AppPackageDir = Join-Path $WindowsDir "XRDAnalyzer\AppPackages\"
    $BuildArgs += "/p:GenerateAppxPackageOnBuild=true"
    $BuildArgs += "/p:AppxPackageDir=$AppPackageDir"
}
Write-Host "==> dotnet build $AppProject -c $Configuration -r $RuntimeIdentifier"
& dotnet build @BuildArgs
if ($LASTEXITCODE -ne 0) { throw "dotnet build failed" }

Write-Host "Done. Run: windows\XRDAnalyzer\bin\$Configuration\net8.0-windows10.0.26100.0\$RuntimeIdentifier\XRDAnalyzer.exe"
if ($Msix) { Write-Host "MSIX package(s): windows\XRDAnalyzer\AppPackages\" }

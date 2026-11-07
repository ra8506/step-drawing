# Builds build\app (bundled Python + app + launcher) and dist\STEP-Drawing-<ver>-Setup.exe.
# Usage (Windows, PowerShell, needs Python 3.13 and Inno Setup 6 on PATH):  .\packaging\build_windows.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$pyVer = "3.13.7"
$version = (Select-String -Path stepdraw\__init__.py -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
Write-Host "Building STEP Drawing $version"

Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
$app = "build\app"
$py = "$app\python"
New-Item -ItemType Directory -Force $py | Out-Null

# 1. Embedded CPython runtime
$zip = "build\python-embed.zip"
Invoke-WebRequest "https://www.python.org/ftp/python/$pyVer/python-$pyVer-embed-amd64.zip" -OutFile $zip
Expand-Archive $zip -DestinationPath $py
$pth = Get-ChildItem "$py\python3*._pth" | Select-Object -First 1
@("python313.zip", ".", "Lib\site-packages", "import site") | Set-Content $pth.FullName -Encoding ascii

# 2. Dependencies + app code into the runtime's site-packages
python -m pip install --upgrade pip
python -m pip install --only-binary=:all: --python-version 3.13 --platform win_amd64 --implementation cp `
    --target "$py\Lib\site-packages" -r requirements.txt
Copy-Item -Recurse stepdraw "$py\Lib\site-packages\stepdraw"
Get-ChildItem -Recurse -Directory -Filter __pycache__ "$py\Lib\site-packages" | Remove-Item -Recurse -Force
# Trim Qt pieces the app never uses (keeps the installer smaller)
$qt = "$py\Lib\site-packages\PySide6"
Remove-Item -Recurse -Force "$qt\translations", "$qt\examples", "$qt\include", "$qt\qml" -ErrorAction SilentlyContinue
Remove-Item -Force "$qt\designer.exe", "$qt\linguist.exe", "$qt\assistant.exe", "$qt\lupdate.exe", "$qt\lrelease.exe" -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$app\assets" | Out-Null
Copy-Item assets\icon.ico "$app\assets\icon.ico"
Copy-Item -Recurse tests\samples "$app\samples"

# 3. Launcher exe
$csc = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
& $csc /nologo /target:winexe /optimize "/win32icon:assets\icon.ico" "/out:$app\STEP Drawing.exe" `
    /reference:System.Windows.Forms.dll packaging\launcher.cs
if ($LASTEXITCODE -ne 0) { throw "launcher build failed" }

# 4. Smoke test the bundled runtime (headless)
$env:QT_QPA_PLATFORM = "offscreen"
& "$py\python.exe" -m stepdraw --cli "$app\samples\bracket.step" -o build\smoke.pdf -o build\smoke.dxf -o build\smoke.svg
if ($LASTEXITCODE -ne 0 -or -not (Test-Path build\smoke.pdf)) { throw "smoke test failed" }
# Worker processes (spawned from both python.exe and the windowless pythonw.exe the app runs under)
& "$py\python.exe" -m stepdraw --selftest "$app\samples\plate.step"
if ($LASTEXITCODE -ne 0) { throw "worker self-test failed (python.exe)" }
$p = Start-Process "$py\pythonw.exe" -ArgumentList "-m","stepdraw","--selftest","`"$app\samples\plate.step`"" -PassThru -Wait
if ($p.ExitCode -ne 0) { throw "worker self-test failed (pythonw.exe): $($p.ExitCode)" }

# 5. Installer
iscc /Q "/DAppVersion=$version" "/DSourceDir=..\$app" packaging\installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
Get-ChildItem dist

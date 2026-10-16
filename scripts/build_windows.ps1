$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
New-Item -ItemType Directory -Path dist -Force | Out-Null
python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
$python = Join-Path $PWD '.venv\Scripts\python.exe'
& $python -m pip install --require-hashes -r requirements-windows.lock
if ($LASTEXITCODE -ne 0) { throw 'Locked runtime dependency installation failed' }
& $python -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& $python -m pytest tests -q --junitxml=dist/windows-tests.xml
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; no release will be published' }
& $python scripts/verify_clean_release.py --fresh-store
if ($LASTEXITCODE -ne 0) { throw 'Fresh-store validation failed' }
& $python -m PyInstaller --noconfirm --clean --windowed --onedir --name GKM --collect-all PySide6 launch.py
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed' }
Copy-Item docs\USER_GUIDE.md dist\GKM\USER_GUIDE.md
Copy-Item docs\ACCEPTANCE.md dist\GKM\ACCEPTANCE.md
Copy-Item docs\THIRD_PARTY.md dist\GKM\THIRD_PARTY.md
Copy-Item docs\RELEASE_NOTES.md dist\GKM\RELEASE_NOTES.md
Get-Content docs\USER_GUIDE.md -Raw | Set-Content dist\GKM\README.txt -Encoding utf8BOM
& $python scripts/verify_clean_release.py --package-dir dist/GKM
if ($LASTEXITCODE -ne 0) { throw 'Package contains forbidden user or test data' }
Compress-Archive -Path dist\GKM -DestinationPath dist\GKM-Windows-x64.zip -Force
# Git archive contains only reviewed, committed sources; dist/private files are ignored.
git archive --format=zip --prefix=GKM-Source/ -o dist/GKM-Source.zip HEAD
if ($LASTEXITCODE -ne 0) { throw 'Source archiving failed' }
$files = @('GKM-Windows-x64.zip', 'GKM-Source.zip', 'windows-tests.xml')
$lines = foreach ($file in $files) {
    $hash = (Get-FileHash (Join-Path dist $file) -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $file"
}
$lines | Set-Content dist\SHA256SUMS.txt -Encoding ascii

@echo off
setlocal

rem Renomeia todos os arquivos da pasta atual para "YYYYMMDD - HHMM - nome original"
rem usando a data/hora de criacao do arquivo. Ignora este .bat e arquivos ja renomeados.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$self = (Split-Path -Leaf $MyInvocation.MyCommand.Path);" ^
  "Get-ChildItem -File -LiteralPath '%~dp0.' | ForEach-Object {" ^
  "  if ($_.Name -ieq 'rename_by_creation.bat') { return }" ^
  "  if ($_.Name -match '^\d{8} - \d{4} - ') { return }" ^
  "  $prefix = $_.CreationTime.ToString('yyyyMMdd - HHmm');" ^
  "  $newName = '{0} - {1}' -f $prefix, $_.Name;" ^
  "  $target = Join-Path $_.DirectoryName $newName;" ^
  "  if (Test-Path -LiteralPath $target) {" ^
  "    Write-Host ('SKIP (ja existe): ' + $newName);" ^
  "  } else {" ^
  "    Rename-Item -LiteralPath $_.FullName -NewName $newName;" ^
  "    Write-Host ('OK: ' + $_.Name + ' -> ' + $newName);" ^
  "  }" ^
  "}"

endlocal
pause

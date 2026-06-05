$launcherPath = "E:\AI股票复盘自动化项目\core\launcher.py"
$pythonPath = "python"
$newShortcut = [Environment]::GetFolderPath('Desktop') + "\通达信(复牌助手).lnk"

Write-Host "Creating shortcut..."
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($newShortcut)
$s.TargetPath = $pythonPath
$s.Arguments = '"' + $launcherPath + '"'
$s.WorkingDirectory = "E:\AI股票复盘自动化项目\core"
$s.Description = "FuPai - Auto update + start TDX"
$s.Save()
Write-Host "Done: " $newShortcut

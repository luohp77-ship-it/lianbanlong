
$taskName = "FuPai_ZT_Update"
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "E:\AI股票复盘自动化项目\core\silent_update.vbs"
$trigger = New-ScheduledTaskTrigger -Daily -At 16:00
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Force
Write-Host "Task created: " $taskName

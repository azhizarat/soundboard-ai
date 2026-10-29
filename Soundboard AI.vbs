Option Explicit
Dim WshShell, FSO, strScriptDir, strCmd
Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
strScriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = strScriptDir
strCmd = "uv run --python 3.13 """ & strScriptDir & "\main.py"" --minimized"
WshShell.Run strCmd, 0, False

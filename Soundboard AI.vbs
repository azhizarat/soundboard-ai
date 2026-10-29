Option Explicit
Dim WshShell, FSO, strScriptDir, strCmd, i, strArgs
Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
strScriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = strScriptDir

strArgs = ""
For i = 0 To WScript.Arguments.Count - 1
    strArgs = strArgs & " """ & WScript.Arguments(i) & """"
Next

strCmd = "uv run --python 3.13 """ & strScriptDir & "\main.py""" & strArgs
WshShell.Run strCmd, 0, False
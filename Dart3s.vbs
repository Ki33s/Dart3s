Option Explicit
Dim shell, files, root, python, entry
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
python = root & "\.venv-cli\Scripts\pythonw.exe"
entry = root & "\scripts\launch_gui.pyw"
If Not files.FileExists(python) Then
    MsgBox "Не найден Python приложения: " & python, vbCritical, "Dart3s"
    WScript.Quit 1
End If
shell.CurrentDirectory = root
shell.Run Chr(34) & python & Chr(34) & " " & Chr(34) & entry & Chr(34), 1, False

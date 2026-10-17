Option Explicit
Dim shell, files, root, python, entry
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
python = root & "\.venv-cli\Scripts\pythonw.exe"
entry = root & "\Details3D\launch.pyw"
If Not files.FileExists(python) Then
    MsgBox "Python приложения не найден: " & python, vbCritical, "Details 3D"
    WScript.Quit 1
End If
shell.CurrentDirectory = root
shell.Run Chr(34) & python & Chr(34) & " " & Chr(34) & entry & Chr(34), 1, False

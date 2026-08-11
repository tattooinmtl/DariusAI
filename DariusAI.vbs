' Double-click this to start DariusAI with zero visible window — no console
' flash, no terminal. Runs this project's own .venv\Scripts\pythonw.exe
' (not whatever Python happens to be associated with .pyw system-wide, which
' may not have this project's dependencies installed) against launch.pyw,
' which shows the splash screen and then the app window.

Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = root & "\.venv\Scripts\pythonw.exe"
launcher = root & "\launch.pyw"

Set shell = CreateObject("WScript.Shell")

If Not fso.FileExists(pythonw) Then
    MsgBox "Could not find " & pythonw & vbCrLf & "Run 'python -m venv .venv' and install dependencies first.", vbCritical, "DariusAI"
    WScript.Quit 1
End If

shell.Run """" & pythonw & """ """ & launcher & """", 0, False

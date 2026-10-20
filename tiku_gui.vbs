' ============================================
'  Study Toolbox - Tiku Service Launcher
'  Double-click this file to open the panel.
'  No console window will appear.
'
'  NOTE: keep this file ASCII-only.
'  wscript reads .vbs as ANSI, and UTF-8
'  Chinese comments break the parser.
' ============================================

Option Explicit

Dim fso, sh, baseDir, pyw, target, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = baseDir

target = baseDir & "\tiku_gui.py"
If Not fso.FileExists(target) Then
    MsgBox "Cannot find tiku_gui.py in:" & vbCrLf & baseDir, 16, "Tiku Service"
    WScript.Quit 1
End If

' Prefer pythonw.exe (no console). Fall back to the py launcher.
pyw = "pythonw.exe"
cmd = """" & pyw & """ """ & target & """"

On Error Resume Next
sh.Run cmd, 0, False
If Err.Number <> 0 Then
    Err.Clear
    sh.Run "pyw -3 """ & target & """", 0, False
    If Err.Number <> 0 Then
        Err.Clear
        MsgBox "Failed to start. Please check that Python is installed" & vbCrLf & _
               "and that pythonw.exe is on PATH.", 16, "Tiku Service"
    End If
End If
On Error Goto 0

"""Small native pop-ups for the tray: text input (optionally hidden, for
passwords), confirmations, alerts and notifications. macOS uses the system's
AppleScript dialogs, Windows a minimal Windows Forms window. Values are passed
as arguments or environment variables, never pasted into script source."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading

_JXA = r"""function run(argv) {
  var kind = argv[0], title = argv[1], msg = argv[2], def = argv[3], ok = argv[4], cancel = argv[5];
  var app = Application.currentApplication();
  app.includeStandardAdditions = true;
  if (kind === "notify") { app.displayNotification(msg, {withTitle: title}); return "OK"; }
  app.activate();
  try {
    if (kind === "input" || kind === "secret") {
      var r = app.displayDialog(msg, {withTitle: title, defaultAnswer: def, hiddenAnswer: kind === "secret",
        buttons: [cancel, ok], defaultButton: ok, cancelButton: cancel});
      return "OK\n" + r.textReturned;
    }
    if (kind === "confirm") {
      app.displayDialog(msg, {withTitle: title, buttons: [cancel, ok], defaultButton: ok, cancelButton: cancel});
      return "OK";
    }
    app.displayDialog(msg, {withTitle: title, buttons: [ok], defaultButton: ok});
    return "OK";
  } catch (e) {
    return "CANCEL";
  }
}"""

# A minimal Windows Forms dialog. It reads everything from KP_* environment
# variables, so no value is ever parsed as PowerShell code, and writes
# "OK\n<text>" (UTF-8) when confirmed.
_FORM = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
$kind = $env:KP_KIND
$f = New-Object Windows.Forms.Form
$f.Text = $env:KP_TITLE; $f.TopMost = $true; $f.StartPosition = 'CenterScreen'
$f.FormBorderStyle = 'FixedDialog'; $f.MaximizeBox = $false; $f.MinimizeBox = $false
$f.AutoSize = $true; $f.AutoSizeMode = 'GrowAndShrink'; $f.Padding = New-Object Windows.Forms.Padding 12
$f.Font = New-Object Drawing.Font 'Segoe UI', 9
$p = New-Object Windows.Forms.FlowLayoutPanel
$p.FlowDirection = 'TopDown'; $p.AutoSize = $true; $p.WrapContents = $false
$l = New-Object Windows.Forms.Label
$l.Text = $env:KP_MSG; $l.AutoSize = $true; $l.MaximumSize = New-Object Drawing.Size 420, 0; $l.Margin = New-Object Windows.Forms.Padding 0, 0, 0, 10
$p.Controls.Add($l)
$t = $null
if ($kind -eq 'input' -or $kind -eq 'secret') {
  $t = New-Object Windows.Forms.TextBox
  $t.Width = 420; $t.Text = $env:KP_DEF; $t.UseSystemPasswordChar = ($kind -eq 'secret')
  $p.Controls.Add($t)
}
$b = New-Object Windows.Forms.FlowLayoutPanel
$b.FlowDirection = 'RightToLeft'; $b.AutoSize = $true; $b.Width = 420; $b.Margin = New-Object Windows.Forms.Padding 0, 12, 0, 0
$ok = New-Object Windows.Forms.Button
$ok.Text = $env:KP_OK; $ok.AutoSize = $true; $ok.DialogResult = 'OK'
$b.Controls.Add($ok); $f.AcceptButton = $ok
if ($env:KP_CANCEL) {
  $c = New-Object Windows.Forms.Button
  $c.Text = $env:KP_CANCEL; $c.AutoSize = $true; $c.DialogResult = 'Cancel'
  $b.Controls.Add($c); $f.CancelButton = $c
}
$p.Controls.Add($b)
$f.Controls.Add($p)
$f.Add_Shown({ $f.Activate(); if ($t) { $t.Focus() } })
if ($f.ShowDialog() -eq 'OK') {
  if ($t) { [Console]::Out.Write("OK`n" + $t.Text) } else { [Console]::Out.Write('OK') }
} else {
  [Console]::Out.Write('CANCEL')
}
"""

# A Windows notification shown as PowerShell (no app registration needed).
_TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$n = $x.GetElementsByTagName('text')
$n.Item(0).AppendChild($x.CreateTextNode($env:KP_TITLE)) | Out-Null
$n.Item(1).AppendChild($x.CreateTextNode($env:KP_MSG)) | Out-Null
$id = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show([Windows.UI.Notifications.ToastNotification]::new($x))
"""


def _powershell(script_path: str) -> list[str]:
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-STA",
            "-WindowStyle", "Hidden", "-File", script_path]


def _run_powershell(script: str, env: dict[str, str], answers: tuple[str, ...] = ("OK", "CANCEL"), attempts: int = 3) -> str | None:
    """Runs a script and returns what it printed. The script goes through a file, not -EncodedCommand: on some
    machines Windows starts an encoded PowerShell and ends it at once, printing nothing (seen here on every
    second launch). Output that is no known answer means the launch failed, so it is tried again."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "keypad-dialog.ps1")
        with open(path, "w", encoding="utf-8-sig") as f:  # the BOM makes Windows PowerShell read it as UTF-8
            f.write(script)
        for _ in range(attempts):
            r = subprocess.run(_powershell(path), capture_output=True, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
            out = r.stdout.decode("utf-8", errors="replace").rstrip("\r\n")
            if out.startswith(answers):
                return out
    return None


def _show(kind: str, title: str, msg: str, default: str = "", ok: str = "OK", cancel: str = "Cancel") -> str | None:
    """The entered text (or "") when confirmed; None when cancelled."""
    try:
        if sys.platform == "darwin":
            r = subprocess.run(["osascript", "-l", "JavaScript", "-e", _JXA, kind, title, msg, default, ok, cancel],
                               capture_output=True, text=True)
            out = r.stdout.rstrip("\n")
        elif sys.platform == "win32":
            env = {**os.environ, "KP_KIND": kind, "KP_TITLE": title, "KP_MSG": msg, "KP_DEF": default,
                   "KP_OK": ok, "KP_CANCEL": cancel}
            out = _run_powershell(_FORM, env) or "CANCEL"
        else:
            return _zenity(kind, title, msg, default, ok, cancel)
    except OSError:
        return None
    if not out.startswith("OK"):
        return None
    return out.partition("\n")[2]


def _zenity(kind, title, msg, default, ok, cancel) -> str | None:
    if not shutil.which("zenity"):
        return None
    if kind in ("input", "secret"):
        args = ["--entry", "--text", msg, "--entry-text", default] + (["--hide-text"] if kind == "secret" else [])
    elif kind == "confirm":
        args = ["--question", "--text", msg, "--ok-label", ok, "--cancel-label", cancel]
    else:
        args = ["--info", "--text", msg]
    r = subprocess.run(["zenity", "--title", title, *args], capture_output=True, text=True)
    return r.stdout.rstrip("\n") if r.returncode == 0 else None


def input(title: str, message: str, label: str = "", value: str = "", secret: bool = False) -> str | None:  # noqa: A001
    """Asks for one value; None when the user cancelled."""
    msg = message + (f"\n\n{label}" if label else "")
    v = _show("secret" if secret else "input", title, msg, value)
    return None if v is None else v.replace("\r", "").strip()


def confirm(title: str, message: str, yes: str, no: str) -> bool:
    return _show("confirm", title, message, ok=yes, cancel=no) is not None


def alert(title: str, message: str) -> None:
    _show("alert", title, message, cancel="")


def notify(title: str, message: str) -> None:
    """A passing notification (no buttons, doesn't block)."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["osascript", "-l", "JavaScript", "-e", _JXA, "notify", title, message, "", "", ""],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform == "win32":
            env = {**os.environ, "KP_TITLE": title, "KP_MSG": message}
            threading.Thread(target=_run_powershell, args=(_TOAST, env, ("",), 1), daemon=True).start()
        elif shutil.which("notify-send"):
            subprocess.Popen(["notify-send", title, message])
    except OSError:
        pass

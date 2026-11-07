// Tiny Windows launcher: "STEP Drawing.exe" starts the bundled Python runtime with the app.
// Built in CI with the .NET Framework compiler (csc /target:winexe /win32icon:assets\icon.ico).
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Windows.Forms;

static class Launcher
{
    [STAThread]
    static int Main(string[] args)
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        bool cli = args.Length > 0 && args[0] == "--cli";
        string py = Path.Combine(dir, "python", cli ? "python.exe" : "pythonw.exe");
        if (!File.Exists(py))
        {
            MessageBox.Show("The STEP Drawing runtime is missing. Please reinstall the app.", "STEP Drawing",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        string quoted = string.Join(" ", args.Select(a => "\"" + a.Replace("\"", "\\\"") + "\""));
        var psi = new ProcessStartInfo(py, "-m stepdraw " + quoted)
        {
            UseShellExecute = false,
            WorkingDirectory = dir,
        };
        var p = Process.Start(psi);
        if (cli) { p.WaitForExit(); return p.ExitCode; }
        return 0;
    }
}

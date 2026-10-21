// JetAI.exe - double-click launcher. Starts jetai.ps1 (the local server + app window) from the folder this
// file sits in. If JetAI is already running it only opens the app window again. No installer, no admin
// rights, nothing downloaded. Build: .\scripts\build-exe.ps1 (uses the C# compiler that ships with Windows).
using System;
using System.Diagnostics;
using System.IO;
using System.Net.Sockets;
using System.Threading;
using System.Windows.Forms;

static class JetAILauncher
{
    const int DefaultPort = 8787;

    [STAThread]
    static int Main(string[] args)
    {
        int port = DefaultPort;
        if (args.Length >= 2 && args[0] == "-Port") int.TryParse(args[1], out port);
        if (port < 1024 || port > 65535) port = DefaultPort;

        string dir = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(dir, "jetai.ps1");
        if (!File.Exists(script))
        {
            Fail("jetai.ps1 을 찾을 수 없습니다.\nJetAI.exe 는 jetai.ps1 과 같은 폴더에 있어야 합니다.\n\n" + dir);
            return 2;
        }

        if (IsListening(port))
        {
            OpenAppWindow(port);   // already running: just bring up a window
            return 0;
        }

        var psi = new ProcessStartInfo
        {
            FileName = Path.Combine(Environment.SystemDirectory, @"WindowsPowerShell\v1.0\powershell.exe"),
            Arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\" -Port " + port,
            WorkingDirectory = dir,
            UseShellExecute = true,
            WindowStyle = ProcessWindowStyle.Minimized   // the console hosts the server; closing it stops JetAI
        };
        Process server;
        try { server = Process.Start(psi); }
        catch (Exception e) { Fail("PowerShell 을 시작하지 못했습니다.\n\n" + e.Message); return 3; }

        // jetai.ps1 exits right away when something is wrong (for example Python 3.10+ is missing).
        if (server.WaitForExit(4000))
        {
            Fail("JetAI 를 시작하지 못했습니다 (종료 코드 " + server.ExitCode + ").\n\n" +
                 "가장 흔한 원인: Python 3.10 이상이 설치되어 있지 않음 (python.org 에서 설치할 때 'Add python.exe to PATH' 체크),\n" +
                 "또는 포트 " + port + " 사용 중.\n\n" +
                 "자세한 메시지는 PowerShell 에서 이 폴더의 .\\jetai.ps1 을 실행하면 볼 수 있습니다.");
            return 4;
        }
        return 0;
    }

    static bool IsListening(int port)
    {
        try
        {
            using (var client = new TcpClient())
            {
                var pending = client.BeginConnect("127.0.0.1", port, null, null);
                return pending.AsyncWaitHandle.WaitOne(500) && client.Connected;
            }
        }
        catch { return false; }
    }

    static void OpenAppWindow(int port)
    {
        string url = "http://127.0.0.1:" + port + "/app";
        try { Process.Start(new ProcessStartInfo("msedge", "--app=" + url) { UseShellExecute = true }); }
        catch { try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); } catch { } }
    }

    static void Fail(string message)
    {
        MessageBox.Show(message, "JetAI", MessageBoxButtons.OK, MessageBoxIcon.Warning);
    }
}

// JetAI.exe - double-click launcher. Starts the local server (python -m workshop.dashboard) in the background with
// NO console or PowerShell window, opens the app window, and stays as a tray icon ("열기 / 로그 보기 / 종료") that
// owns the server: quitting from the tray stops it. A second double-click only opens the app window again.
// Uses the Python bundled next to this exe (python\python.exe) or, in a source checkout, the system Python 3.10+.
// Server output goes to %USERPROFILE%\.agent-workshop\logs\jetai-server.log. No installer, no admin rights, nothing
// downloaded. Build: .\scripts\build-exe.ps1 (uses the C# compiler that ships with Windows).
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net.Sockets;
using System.Threading;
using System.Windows.Forms;

static class JetAILauncher
{
    const int DefaultPort = 8787;
    static Process server;
    static NotifyIcon tray;
    static string logPath;

    [STAThread]
    static int Main(string[] args)
    {
        int port = DefaultPort;
        if (args.Length >= 2 && args[0] == "-Port") int.TryParse(args[1], out port);
        if (port < 1024 || port > 65535) port = DefaultPort;

        string dir = AppDomain.CurrentDomain.BaseDirectory;
        if (!File.Exists(Path.Combine(dir, "workshop", "dashboard.py")))
        {
            Fail("workshop 폴더를 찾을 수 없습니다.\nJetAI.exe 는 workshop 폴더와 같은 위치에 있어야 합니다.\n\n" + dir);
            return 2;
        }

        if (IsListening(port)) { OpenAppWindow(port); return 0; }   // already running: just bring up a window

        bool created;
        var mutex = new Mutex(true, "JetAI-Launcher-" + port, out created);
        if (!created) { OpenAppWindow(port); return 0; }              // another launcher is starting the server right now

        string python = FindPython(dir);
        if (python == null)
        {
            Fail("Python 3.10 이상을 찾지 못했습니다.\n\n설치 프로그램(JetAI-Setup.exe)으로 설치하면 Python이 함께 들어 있습니다.\n소스 폴더에서 실행하는 경우 python.org 에서 Python 3.10+ 를 설치하세요('Add python.exe to PATH' 체크).");
            return 3;
        }

        string logDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".agent-workshop", "logs");
        try { Directory.CreateDirectory(logDir); } catch { }
        logPath = Path.Combine(logDir, "jetai-server.log");
        try { if (File.Exists(logPath) && new FileInfo(logPath).Length > 1000000) File.Delete(logPath); } catch { }

        // cmd.exe redirects the server's output to the log file; CreateNoWindow keeps every console hidden.
        var psi = new ProcessStartInfo
        {
            FileName = Path.Combine(Environment.SystemDirectory, "cmd.exe"),
            Arguments = "/c \"\"" + python + "\" -X utf8 -m workshop.dashboard --port " + port + " >> \"" + logPath + "\" 2>&1\"",
            WorkingDirectory = dir,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden
        };
        try { server = Process.Start(psi); }
        catch (Exception e) { Fail("서버를 시작하지 못했습니다.\n\n" + e.Message); return 4; }

        DateTime deadline = DateTime.UtcNow.AddSeconds(30);
        while (DateTime.UtcNow < deadline && !IsListening(port))
        {
            if (server.HasExited) break;
            Thread.Sleep(250);
        }
        if (!IsListening(port))
        {
            string tail = ReadTail(logPath, 600);
            StopServer();
            Fail("JetAI 를 시작하지 못했습니다(포트 " + port + " 사용 중이거나 서버 오류).\n\n" + tail + "\n\n로그: " + logPath);
            return 5;
        }

        OpenAppWindow(port);
        Application.EnableVisualStyles();
        tray = new NotifyIcon { Text = "JetAI", Visible = true };
        try { tray.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { tray.Icon = SystemIcons.Application; }
        var menu = new ContextMenuStrip();
        menu.Items.Add("JetAI 열기", null, delegate { OpenAppWindow(port); });
        menu.Items.Add("로그 보기", null, delegate { try { Process.Start("notepad.exe", "\"" + logPath + "\""); } catch { } });
        menu.Items.Add("종료", null, delegate { Quit(); });
        tray.ContextMenuStrip = menu;
        tray.DoubleClick += delegate { OpenAppWindow(port); };
        var watcher = new System.Windows.Forms.Timer { Interval = 2000 };
        watcher.Tick += delegate { if (server != null && server.HasExited) Quit(); };   // the server ended by itself: do not leave an orphan icon
        watcher.Start();
        Application.Run();
        GC.KeepAlive(mutex);
        return 0;
    }

    static void Quit()
    {
        if (tray != null) { tray.Visible = false; tray.Dispose(); tray = null; }
        StopServer();
        Application.ExitThread();
    }

    static void StopServer()
    {
        try
        {
            if (server != null && !server.HasExited)
            {
                // cmd.exe is the parent of python.exe: kill the whole tree.
                var kill = new ProcessStartInfo("taskkill.exe", "/PID " + server.Id + " /T /F") { CreateNoWindow = true, UseShellExecute = false };
                var p = Process.Start(kill);
                if (p != null) p.WaitForExit(5000);
            }
        }
        catch { }
    }

    static string FindPython(string dir)
    {
        string bundled = Path.Combine(dir, "python", "python.exe");
        if (File.Exists(bundled)) return bundled;
        foreach (string name in new[] { "python.exe", "python3.exe" })
        {
            string found = Which(name);
            if (found != null && RunsPython310(found)) return found;
        }
        return null;
    }

    static string Which(string exe)
    {
        string path = Environment.GetEnvironmentVariable("PATH") ?? "";
        foreach (string part in path.Split(';'))
        {
            try
            {
                string full = Path.Combine(part.Trim('"'), exe);
                if (File.Exists(full) && full.IndexOf("WindowsApps", StringComparison.OrdinalIgnoreCase) < 0) return full;   // the Store stub only opens the Store
            }
            catch { }
        }
        return null;
    }

    static bool RunsPython310(string exe)
    {
        try
        {
            var psi = new ProcessStartInfo(exe, "-c \"import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)\"") { CreateNoWindow = true, UseShellExecute = false };
            using (var p = Process.Start(psi)) { return p.WaitForExit(8000) && p.ExitCode == 0; }
        }
        catch { return false; }
    }

    static string ReadTail(string path, int chars)
    {
        try
        {
            string text = File.ReadAllText(path);
            return text.Length > chars ? text.Substring(text.Length - chars) : text;
        }
        catch { return ""; }
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

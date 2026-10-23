// JetAI-Setup.exe - installer and uninstaller in one file (C# 5, compiled with the compiler that ships with Windows).
// The app package (portable zip: app + embeddable Python) is embedded as the resource "payload.zip".
// Installs for the current user only (no admin rights) into C:\JetAI by default, adds Start-menu and
// optional desktop shortcuts and an "Apps & features" entry. The former default folder
// %LOCALAPPDATA%\Programs\JetAI is removed after a new install succeeded; any other folder is left alone. The uninstaller never touches the data folder
// (%USERPROFILE%\.agent-workshop: keys, chats, usage).
// Command line: /S silent, /D=<folder> target, /nodesktop, /nostart, /uninstall (silent with /S).
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Windows.Forms;
using Microsoft.Win32;

static class JetAISetup
{
    const string AppName = "JetAI";
    const string Prefix = "JetAI-portable/";
    const string UninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\JetAI";
    const string Marker = "PACKAGE.txt";           // a folder without this file is never wiped by the uninstaller

    static bool silent, desktopLink = true, startLink = true;
    static string target = @"C:\JetAI";                // default; a normal user may create folders directly under C:\ (no admin rights)

    [STAThread]
    static int Main(string[] args)
    {
        bool uninstall = false;
        foreach (string a in args)
        {
            string l = a.ToLowerInvariant();
            if (l == "/s") silent = true;
            else if (l == "/uninstall") uninstall = true;
            else if (l == "/nodesktop") desktopLink = false;
            else if (l == "/nostart") startLink = false;
            else if (l.StartsWith("/d=")) target = a.Substring(3).Trim('"');
        }
        if (Path.GetFileName(Application.ExecutablePath).Equals("Uninstall.exe", StringComparison.OrdinalIgnoreCase)) uninstall = true;
        Application.EnableVisualStyles();
        try
        {
            if (uninstall) return Uninstall();
            if (silent) { Install(delegate (int p) { }); return 0; }
            Application.Run(new SetupForm());
            return 0;
        }
        catch (Exception e)
        {
            if (!silent) MessageBox.Show(e.Message, AppName, MessageBoxButtons.OK, MessageBoxIcon.Error);
            Console.Error.WriteLine(e.Message);
            return 1;
        }
    }

    static string StartMenuDir { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), AppName); } }
    static string DesktopLink { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), AppName + ".lnk"); } }

    public static void Install(Action<int> progress)
    {
        string full = Path.GetFullPath(target);
        if (full.Length < 4 || Path.GetPathRoot(full) == full) throw new InvalidOperationException("설치 폴더가 올바르지 않습니다: " + full);
        if (Directory.Exists(full) && Directory.GetFileSystemEntries(full).Length > 0 && !File.Exists(Path.Combine(full, Marker)))
            throw new InvalidOperationException("이 폴더에는 이미 다른 파일이 있어 설치하지 않았습니다.\n빈 폴더이거나 JetAI 폴더를 고르세요.\n\n" + full);
        target = full;
        string previous = null;
        using (RegistryKey old = Registry.CurrentUser.OpenSubKey(UninstallKey))
            if (old != null) previous = old.GetValue("InstallLocation") as string;
        Directory.CreateDirectory(full);
        Assembly me = Assembly.GetExecutingAssembly();
        using (Stream payload = me.GetManifestResourceStream("payload.zip"))
        {
            if (payload == null) throw new InvalidOperationException("설치 파일 안에 앱 패키지가 없습니다(빌드 오류).");
            using (var zip = new ZipArchive(payload, ZipArchiveMode.Read))
            {
                int done = 0;
                foreach (ZipArchiveEntry entry in zip.Entries)
                {
                    done++;
                    string rel = entry.FullName.Replace('\\', '/');
                    if (rel.StartsWith(Prefix)) rel = rel.Substring(Prefix.Length);
                    if (rel.Length == 0) continue;
                    string dest = Path.GetFullPath(Path.Combine(full, rel));
                    if (!dest.StartsWith(full + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) continue;   // zip-slip guard
                    if (rel.EndsWith("/")) { Directory.CreateDirectory(dest); continue; }
                    Directory.CreateDirectory(Path.GetDirectoryName(dest));
                    try { entry.ExtractToFile(dest, true); }
                    catch (IOException) { throw new IOException("파일을 쓸 수 없습니다. JetAI가 실행 중이면 닫고 다시 시도하세요.\n" + dest); }
                    progress(done * 90 / zip.Entries.Count);
                }
            }
        }
        File.Copy(Application.ExecutablePath, Path.Combine(full, "Uninstall.exe"), true);
        string exe = Path.Combine(full, "JetAI.exe");
        string icon = File.Exists(exe) ? exe : null;
        if (startLink) { Directory.CreateDirectory(StartMenuDir); MakeLink(Path.Combine(StartMenuDir, AppName + ".lnk"), full, exe); }
        if (desktopLink) MakeLink(DesktopLink, full, exe);
        string version = "1.0";
        try { foreach (string line in File.ReadAllLines(Path.Combine(full, "VERSION.txt"))) { if (line.Trim().Length > 0) { version = line.Trim(); break; } } } catch { }
        using (RegistryKey key = Registry.CurrentUser.CreateSubKey(UninstallKey))
        {
            key.SetValue("DisplayName", AppName);
            key.SetValue("DisplayVersion", version);
            key.SetValue("Publisher", "JetAI");
            key.SetValue("InstallLocation", full);
            key.SetValue("UninstallString", "\"" + Path.Combine(full, "Uninstall.exe") + "\" /uninstall");
            key.SetValue("QuietUninstallString", "\"" + Path.Combine(full, "Uninstall.exe") + "\" /uninstall /S");
            if (icon != null) key.SetValue("DisplayIcon", icon);
            key.SetValue("NoModify", 1, RegistryValueKind.DWord);
            key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
        }
        RemoveOldCopy(previous, full);
        progress(100);
    }

    // Only the former default location (%LOCALAPPDATA%\Programs\JetAI) is cleaned up, and only with the marker file; failures (still running) are ignored.
    static void RemoveOldCopy(string previous, string current)
    {
        try
        {
            if (string.IsNullOrEmpty(previous)) return;
            string old = Path.GetFullPath(previous);
            string legacy = Path.GetFullPath(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "JetAI"));
            if (!old.Equals(legacy, StringComparison.OrdinalIgnoreCase)) return;     // only the former default location; a folder the user picked is never deleted
            if (old.Equals(current, StringComparison.OrdinalIgnoreCase) || !File.Exists(Path.Combine(old, Marker))) return;
            Directory.Delete(old, true);
        }
        catch { }
    }

    static void MakeLink(string link, string workDir, string exe)
    {
        Type shell = Type.GetTypeFromProgID("WScript.Shell");
        object sh = Activator.CreateInstance(shell);
        object lnk = shell.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, sh, new object[] { link });
        Type t = lnk.GetType();
        t.InvokeMember("TargetPath", BindingFlags.SetProperty, null, lnk, new object[] { exe });
        t.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, lnk, new object[] { workDir });
        t.InvokeMember("IconLocation", BindingFlags.SetProperty, null, lnk, new object[] { exe + ",0" });
        t.InvokeMember("Description", BindingFlags.SetProperty, null, lnk, new object[] { "JetAI" });
        t.InvokeMember("Save", BindingFlags.InvokeMethod, null, lnk, null);
    }

    static int Uninstall()
    {
        string dir = null;
        using (RegistryKey key = Registry.CurrentUser.OpenSubKey(UninstallKey))
            if (key != null) dir = key.GetValue("InstallLocation") as string;
        if (dir == null) dir = Path.GetDirectoryName(Application.ExecutablePath);
        if (!silent && MessageBox.Show("JetAI를 제거할까요?\n\n내 데이터 폴더(키·대화·사용 기록)는 지우지 않습니다.", AppName, MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes) return 0;
        try { if (File.Exists(DesktopLink)) File.Delete(DesktopLink); } catch { }
        try { if (Directory.Exists(StartMenuDir)) Directory.Delete(StartMenuDir, true); } catch { }
        try { Registry.CurrentUser.DeleteSubKeyTree(UninstallKey, false); } catch { }
        if (dir != null && File.Exists(Path.Combine(dir, Marker)))
        {
            // Uninstall.exe is running from inside the folder: delete the rest, then let cmd remove the folder after we exit.
            string self = Application.ExecutablePath;
            foreach (string entry in Directory.GetFileSystemEntries(dir))
            {
                if (entry.Equals(self, StringComparison.OrdinalIgnoreCase)) continue;
                try { if (Directory.Exists(entry)) Directory.Delete(entry, true); else File.Delete(entry); } catch { }
            }
            var psi = new ProcessStartInfo("cmd.exe", "/c ping 127.0.0.1 -n 3 >nul & rmdir /s /q \"" + dir + "\"")
            { CreateNoWindow = true, UseShellExecute = false, WindowStyle = ProcessWindowStyle.Hidden };
            Process.Start(psi);
        }
        if (!silent) MessageBox.Show("JetAI를 제거했습니다. 데이터 폴더(.agent-workshop)는 그대로 있습니다.", AppName);
        return 0;
    }

    sealed class SetupForm : Form
    {
        readonly TextBox path = new TextBox();
        readonly CheckBox desktop = new CheckBox(), start = new CheckBox(), launch = new CheckBox();
        readonly ProgressBar bar = new ProgressBar();
        readonly Button go = new Button();
        readonly Label status = new Label();

        public SetupForm()
        {
            Text = "JetAI 설치"; ClientSize = new Size(520, 280); FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
            var title = new Label { Text = "JetAI 설치", Font = new Font(Font.FontFamily, 14, FontStyle.Bold), Left = 16, Top = 14, Width = 480, Height = 28 };
            var info = new Label { Text = "무료·로컬 AI로 개발 토큰을 아끼는 도구입니다.\n설치는 관리자 권한이 필요 없고, 내 데이터 폴더는 건드리지 않습니다.", Left = 16, Top = 46, Width = 488, Height = 36 };
            path.Text = target; path.Left = 16; path.Top = 96; path.Width = 410;
            var browse = new Button { Text = "찾아보기", Left = 434, Top = 94, Width = 70 };
            browse.Click += delegate { using (var d = new FolderBrowserDialog()) if (d.ShowDialog() == DialogResult.OK) path.Text = Path.Combine(d.SelectedPath, "JetAI"); };
            desktop.Text = "바탕화면 바로가기 만들기"; desktop.Checked = true; desktop.Left = 16; desktop.Top = 130; desktop.Width = 300;
            start.Text = "시작 메뉴에 추가"; start.Checked = true; start.Left = 16; start.Top = 154; start.Width = 300;
            launch.Text = "설치 후 JetAI 실행"; launch.Checked = true; launch.Left = 16; launch.Top = 178; launch.Width = 300;
            bar.Left = 16; bar.Top = 212; bar.Width = 488; bar.Height = 16;
            status.Left = 16; status.Top = 232; status.Width = 380; status.Height = 36;
            go.Text = "설치"; go.Left = 424; go.Top = 238; go.Width = 80; go.Height = 28;
            go.Click += delegate { Run(); };
            Controls.AddRange(new Control[] { title, info, path, browse, desktop, start, launch, bar, status, go });
        }

        void Run()
        {
            go.Enabled = false; target = path.Text.Trim(); desktopLink = desktop.Checked; startLink = start.Checked;
            status.Text = "설치하는 중...";
            Application.DoEvents();
            try
            {
                Install(delegate (int p) { bar.Value = Math.Min(100, p); Application.DoEvents(); });
                status.Text = "설치가 끝났습니다.";
                if (launch.Checked) { try { Process.Start(new ProcessStartInfo(Path.Combine(target, "JetAI.exe")) { WorkingDirectory = target }); } catch { } }
                string desktopNote = desktopLink ? "\n바탕화면에 'JetAI' 바로가기를 만들었습니다." : "";
                string startNote = startLink ? "\n시작 메뉴에서 'JetAI'를 검색해도 됩니다." : "";
                MessageBox.Show("JetAI를 설치했습니다.\n\n실행 파일: " + Path.Combine(target, "JetAI.exe") + desktopNote + startNote + "\n\n확인을 누르면 설치 폴더를 엽니다.", AppName);
                try { Process.Start("explorer.exe", "\"" + target + "\""); } catch { }
                Close();
            }
            catch (Exception e)
            {
                status.Text = "실패: " + e.Message; go.Enabled = true;
                MessageBox.Show(e.Message, AppName, MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }
    }
}

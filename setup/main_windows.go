package main

import (
	"archive/zip"
	_ "embed"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"time"
)

//go:embed payload/WoWForeverAddons.pyw
var appPyw []byte

//go:embed payload/README.txt
var readmeTxt []byte

//go:embed payload/icon.ico
var iconIco []byte

//go:embed payload/launcher.exe
var launcherExe []byte

//go:embed payload/WebView2Loader.dll
var webView2Loader []byte // Microsoft's WebView2 loader (signed by Microsoft), used by the app window

const appExeName = "WoW Forever Addons.exe"

var appVersion = "dev" // set at build time from APP_VERSION in WoWForeverAddons.pyw

const (
	pyVersion      = "3.13.16"
	appTitle       = "WoW Forever Addon Manager"
	uninstKey      = `Software\Microsoft\Windows\CurrentVersion\Uninstall\WoWForeverAddons`
	lnkName        = "WoW Forever Addons.lnk"
	pyMarker       = ".wfam-python"
	createNoWindow = 0x08000000
)

func init() { runtime.LockOSThread() } // the window runs on the main thread

type paths struct {
	dest, py, data, desktop, programs, docs, appData string
}

func getPaths() paths {
	local := folder(csidlLocalAppData, "LOCALAPPDATA", "")
	appData := folder(csidlAppData, "APPDATA", "")
	p := paths{
		dest:     filepath.Join(local, "Programs", "WoW Forever Addons"),
		data:     filepath.Join(appData, "WoWForeverAddonManager"),
		desktop:  folder(csidlDesktopDir, "USERPROFILE", "Desktop"),
		programs: folder(csidlPrograms, "APPDATA", `Microsoft\Windows\Start Menu\Programs`),
		docs:     folder(csidlPersonal, "USERPROFILE", "Documents"),
		appData:  appData,
	}
	if d := os.Getenv("WFAM_DATA_DIR"); d != "" {
		p.data = d
	}
	p.py = filepath.Join(p.dest, "python")
	return p
}

func main() {
	initAPI()
	initHTTP()
	opt := parseArgs(os.Args[1:])
	var err error
	if opt.uninstall {
		err = uninstall(opt)
	} else {
		err = install(opt)
	}
	if err != nil && err != errCancelled {
		if !opt.quiet {
			msgBox(err.Error(), mbIconError)
		}
		os.Exit(1)
	}
}

var errCancelled = errors.New("cancelled")

// ---------------------------------------------------------------------------
// Shared steps
// ---------------------------------------------------------------------------

// stopManager asks a running copy to quit, then ends whatever is left (the manager's Python and
// its window's private browser profile - never the user's normal browser).
func stopManager(p paths) {
	if st, err := httpDo("POST", "http://127.0.0.1:47811/api/quit", "X-WFAM: 1\r\nContent-Type: application/json\r\n",
		[]byte("{}"), 3000, nil, nil); err == nil && st > 0 {
		time.Sleep(2 * time.Second)
	}
	procs := managerProcesses(p.dest)
	for _, pr := range procs {
		killProcess(pr.pid)
	}
	if len(procs) > 0 {
		time.Sleep(700 * time.Millisecond)
	}
}

func cleanTemp(except string) {
	matches, _ := filepath.Glob(filepath.Join(os.TempDir(), "wfam-*"))
	for _, m := range matches {
		if except == "" || !strings.EqualFold(m, except) {
			removeHard(m, 2)
		}
	}
}

func dirSizeKB(dir string) uint32 {
	var total int64
	filepath.Walk(dir, func(_ string, info os.FileInfo, err error) error {
		if err == nil && !info.IsDir() {
			total += info.Size()
		}
		return nil
	})
	return uint32(total / 1024)
}

func exists(p string) bool { _, err := os.Stat(p); return err == nil }

// ---------------------------------------------------------------------------
// Install / update
// ---------------------------------------------------------------------------

func install(opt options) error {
	p := getPaths()
	old := regGet(uninstKey, "DisplayVersion")
	if !opt.quiet {
		what := "Install " + appTitle + " " + appVersion + "?"
		if old != "" {
			what = "Update " + appTitle + " from " + old + " to " + appVersion + "?"
		}
		if !ask(what + "\n\nIt is installed for your Windows user only (no administrator rights) in:\n" + p.dest +
			"\n\nThe first time, it downloads Python from python.org (about 11 MB) and checks its signature." +
			"\nYour settings, API keys and addons are kept.") {
			return errCancelled
		}
	}
	err := run(opt.quiet, appTitle+" Setup", "Getting ready...", func(pr *progress) error { return doInstall(p, pr) })
	if err != nil {
		return fmt.Errorf("Installation failed:\n\n%v", err)
	}
	if !opt.quiet || opt.restart { // "/restart": the program updated itself and opens again
		var args []string
		if opt.restart && opt.tray { // updated in the background: come back in the tray, not in the user's face
			args = append(args, "--tray")
		}
		cmd := exec.Command(filepath.Join(p.dest, appExeName), args...)
		cmd.Dir = p.dest
		if cmd.Start() != nil {
			msgBox(appTitle+" "+appVersion+" is installed. Start it from the desktop or the Start menu.", mbIconInformation)
		}
	}
	return nil
}

func doInstall(p paths, pr *progress) error {
	pr.status("Closing the manager if it is running...")
	pr.set(20)
	stopManager(p)

	arch := nativeArch()
	want := pyVersion + "-" + arch
	if exists(p.dest) {
		pr.status("Removing the old version...")
		pyOK := false
		if b, err := os.ReadFile(filepath.Join(p.py, pyMarker)); err == nil && strings.TrimSpace(string(b)) == want {
			pyOK = signedByPSF(filepath.Join(p.py, "python.exe")) == nil && signedByPSF(filepath.Join(p.py, "pythonw.exe")) == nil
		}
		entries, err := os.ReadDir(p.dest)
		if err != nil {
			return err
		}
		for _, e := range entries {
			if pyOK && strings.EqualFold(e.Name(), "python") {
				continue
			}
			if !removeHard(filepath.Join(p.dest, e.Name()), 8) {
				return fmt.Errorf("could not remove %s - is something still using it? Close it and run Setup again.", filepath.Join(p.dest, e.Name()))
			}
		}
		regDeleteTree(uninstKey)
		removeHard(filepath.Join(p.data, "window"), 3) // the app window's browser cache: start fresh
		removeHard(filepath.Join(p.data, "webview"), 3)
	}
	self, _ := os.Executable()
	cleanTemp(self) // (the program's own update download may be this very file)
	pr.set(60)
	if err := os.MkdirAll(p.dest, 0755); err != nil {
		return err
	}

	if !exists(filepath.Join(p.py, "pythonw.exe")) {
		if err := installPython(p, arch, want, pr); err != nil {
			return err
		}
	}
	pr.set(880)

	pr.status("Copying the manager...")
	pyw := filepath.Join(p.dest, "WoWForeverAddons.pyw")
	ico := filepath.Join(p.dest, "icon.ico")
	appExe := filepath.Join(p.dest, appExeName)
	files := map[string][]byte{pyw: appPyw, filepath.Join(p.dest, "README.txt"): readmeTxt, ico: iconIco, appExe: launcherExe,
		filepath.Join(p.dest, "WebView2Loader.dll"): webView2Loader}
	for f, b := range files {
		if err := os.WriteFile(f, b, 0644); err != nil {
			return err
		}
	}
	self, err := os.Executable()
	if err != nil {
		return err
	}
	uninstaller := filepath.Join(p.dest, "Uninstall.exe")
	if err := copyFile(self, uninstaller); err != nil {
		return fmt.Errorf("could not copy the uninstaller: %v", err)
	}
	pr.set(920)

	pr.status("Creating shortcuts...")
	err = withCOM(func() error {
		for _, dir := range []string{p.desktop, p.programs} {
			os.MkdirAll(dir, 0755)
			if e := makeShortcut(filepath.Join(dir, lnkName), appExe, "", p.dest, appExe, appTitle); e != nil {
				return e
			}
		}
		return nil
	})
	if err != nil {
		return err
	}
	refreshShell()
	pr.set(960)

	pr.status("Registering in Settings > Apps...")
	k, err := regCreate(uninstKey)
	if err != nil {
		return fmt.Errorf("could not write the Apps entry: %v", err)
	}
	defer k.close()
	q := func(s string) string { return `"` + s + `"` }
	vals := map[string]string{
		"DisplayName": appTitle, "DisplayVersion": appVersion, "Publisher": appTitle,
		"DisplayIcon": appExe + ",0", "InstallLocation": p.dest, "InstallDate": time.Now().Format("20060102"),
		"UninstallString": q(uninstaller) + " /uninstall", "QuietUninstallString": q(uninstaller) + " /uninstall /quiet",
	}
	for n, v := range vals {
		if err := k.setString(n, v); err != nil {
			return fmt.Errorf("could not write the Apps entry: %v", err)
		}
	}
	k.setDword("EstimatedSize", dirSizeKB(p.dest))
	k.setDword("NoModify", 1)
	k.setDword("NoRepair", 1)
	pr.status("Done!")
	pr.set(1000)
	return nil
}

func installPython(p paths, arch, want string, pr *progress) error {
	url := fmt.Sprintf("https://www.python.org/ftp/python/%s/python-%s-embed-%s.zip", pyVersion, pyVersion, arch)
	tmp, err := os.MkdirTemp("", "wfam-python-")
	if err != nil {
		return err
	}
	defer removeHard(tmp, 3)
	zipPath := filepath.Join(tmp, "python.zip")
	pr.status("Downloading Python " + pyVersion + " from python.org (one time only)...")
	if err := download(url, zipPath, func(done, total int64) {
		if total > 0 {
			pr.set(60 + int(done*740/total))
		}
	}); err != nil {
		return fmt.Errorf("could not download Python from python.org: %v", err)
	}
	pr.status("Checking Python's signature...")
	out := filepath.Join(tmp, "python")
	if err := unzip(zipPath, out); err != nil {
		return fmt.Errorf("the Python download is damaged: %v", err)
	}
	for _, exe := range []string{"python.exe", "pythonw.exe"} {
		if !exists(filepath.Join(out, exe)) {
			return fmt.Errorf("the Python download is incomplete (%s missing)", exe)
		}
		if err := signedByPSF(filepath.Join(out, exe)); err != nil {
			return fmt.Errorf("not installing the downloaded Python: %v", err)
		}
	}
	if err := os.WriteFile(filepath.Join(out, pyMarker), []byte(want), 0644); err != nil {
		return err
	}
	removeHard(p.py, 5)
	if err := os.Rename(out, p.py); err != nil {
		if err := copyDir(out, p.py); err != nil {
			return err
		}
	}
	return nil
}

func download(url, dst string, prog func(done, total int64)) error {
	f, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer f.Close()
	st, err := httpDo("GET", url, "", nil, 60000, f, prog)
	if err != nil {
		return err
	}
	if st != 200 {
		return fmt.Errorf("python.org answered HTTP %d", st)
	}
	return f.Close()
}

func unzip(src, dst string) error {
	z, err := zip.OpenReader(src)
	if err != nil {
		return err
	}
	defer z.Close()
	for _, f := range z.File {
		if !safeZipName(f.Name) {
			return fmt.Errorf("unsafe path in zip: %s", f.Name)
		}
		target := filepath.Join(dst, filepath.FromSlash(f.Name))
		if f.FileInfo().IsDir() {
			os.MkdirAll(target, 0755)
			continue
		}
		if err := os.MkdirAll(filepath.Dir(target), 0755); err != nil {
			return err
		}
		r, err := f.Open()
		if err != nil {
			return err
		}
		w, err := os.Create(target)
		if err != nil {
			r.Close()
			return err
		}
		_, err = io.Copy(w, r)
		r.Close()
		if cerr := w.Close(); err == nil {
			err = cerr
		}
		if err != nil {
			return err
		}
	}
	return nil
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		return err
	}
	return out.Close()
}

func copyDir(src, dst string) error {
	return filepath.Walk(src, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(src, path)
		t := filepath.Join(dst, rel)
		if info.IsDir() {
			return os.MkdirAll(t, 0755)
		}
		return copyFile(path, t)
	})
}

// ---------------------------------------------------------------------------
// Uninstall
// ---------------------------------------------------------------------------

func uninstall(opt options) error {
	p := getPaths()
	self, _ := os.Executable()

	// Windows can't delete a running program, so run from a temporary copy.
	if !opt.relocated && underDir(self, p.dest) {
		tmp := filepath.Join(os.TempDir(), fmt.Sprintf("wfam-uninstall-%d.exe", os.Getpid()))
		if err := copyFile(self, tmp); err == nil {
			args := append([]string{"/uninstall", "/relocated"}, quietArg(opt)...)
			cmd := exec.Command(tmp, args...)
			cmd.Dir = os.TempDir()
			if cmd.Start() == nil {
				return nil
			}
		}
	}

	deleteBackups := true
	if !opt.quiet {
		if !ask("Uninstall " + appTitle + "?\n\nThis removes the program, its shortcuts and registry entries, and all of " +
			"its own files: settings, API keys, saved addon versions and temporary files.\n\n" +
			"Your addons in WoW stay installed and keep working.") {
			return errCancelled
		}
		zips, _ := filepath.Glob(filepath.Join(p.data, "backups", "WTF-*.zip"))
		if len(zips) > 0 {
			deleteBackups = ask(fmt.Sprintf("You have %d backup(s) of your addon settings (the WoW WTF folder).\n\n"+
				"Delete them too?\n\nYes = delete them\nNo = move them to %s", len(zips),
				filepath.Join(p.docs, "WoW Forever WTF backups")))
		}
	}
	var kept string
	err := run(opt.quiet, "Uninstall "+appTitle, "Closing the manager...", func(pr *progress) error {
		kept = doUninstall(p, deleteBackups, self, pr)
		return nil
	})
	if err != nil {
		return err
	}
	// Remove whatever was still locked, and this temporary uninstaller, a moment after it exits.
	cmdLine := fmt.Sprintf(`"%s" /c ping -n 3 127.0.0.1 >nul & rmdir /s /q "%s" & rmdir /s /q "%s"`,
		filepath.Join(sys32, "cmd.exe"), p.dest, p.data)
	if underDir(self, os.TempDir()) {
		cmdLine += fmt.Sprintf(` & del /f /q "%s"`, self)
	}
	c := exec.Command(filepath.Join(sys32, "cmd.exe"))
	c.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CmdLine: cmdLine, CreationFlags: createNoWindow}
	c.Dir = os.TempDir()
	c.Start()
	if !opt.quiet {
		msg := appTitle + " has been removed completely.\n\nYour addons in WoW were not touched."
		if kept != "" {
			msg += "\n\nYour WTF backups were moved to:\n" + kept
		}
		msgBox(msg, mbIconInformation)
	}
	return nil
}

func quietArg(opt options) []string {
	if opt.quiet {
		return []string{"/quiet"}
	}
	return nil
}

func doUninstall(p paths, deleteBackups bool, self string, pr *progress) (kept string) {
	pr.set(50)
	stopManager(p)

	// Put back anything an interrupted addon install left in the WoW folder (the manager knows how).
	pr.status("Tidying up the WoW folder...")
	pr.set(250)
	pyExe, app := filepath.Join(p.py, "python.exe"), filepath.Join(p.dest, "WoWForeverAddons.pyw")
	if exists(pyExe) && exists(app) && exists(p.data) {
		c := exec.Command(pyExe, app, "--cleanup")
		c.Dir = p.dest
		c.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindow}
		if c.Start() == nil {
			done := make(chan struct{})
			go func() { c.Wait(); close(done) }()
			select {
			case <-done:
			case <-time.After(60 * time.Second):
				c.Process.Kill()
			}
		}
	}

	pr.status("Removing shortcuts...")
	pr.set(450)
	marks := []string{p.dest, "WoWForeverAddonManager", "WoWForeverAddons.pyw", "127.0.0.1:47811"}
	ql := filepath.Join(p.appData, `Microsoft\Internet Explorer\Quick Launch`)
	for _, dir := range []string{p.desktop, p.programs, ql, filepath.Join(ql, `User Pinned\TaskBar`),
		filepath.Join(ql, `User Pinned\StartMenu`), filepath.Join(ql, `User Pinned\ImplicitAppShortcuts`)} {
		filepath.Walk(dir, func(path string, info os.FileInfo, err error) error {
			if err != nil {
				return nil
			}
			if info.IsDir() {
				if path != dir && !underDir(path, filepath.Join(ql, `User Pinned`)) {
					return filepath.SkipDir // only look one level deep outside the pin folders
				}
				return nil
			}
			if !strings.EqualFold(filepath.Ext(path), ".lnk") || info.Size() > 1<<20 {
				return nil
			}
			ours := strings.EqualFold(info.Name(), lnkName)
			if !ours {
				if b, err := os.ReadFile(path); err == nil {
					ours = lnkMentions(b, marks)
				}
			}
			if ours {
				os.Remove(path)
			}
			return nil
		})
	}
	refreshShell()

	pr.status("Removing registry entries...")
	pr.set(600)
	regDeleteTree(uninstKey)
	// "Start with Windows"
	for _, run := range []string{`Software\Microsoft\Windows\CurrentVersion\Run`,
		`Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run`} {
		if k, ok := regOpen(run, keyReadWrite); ok {
			k.deleteValue("WoWForeverAddons")
			k.close()
		}
	}
	for _, sub := range []string{
		`Software\Classes\Local Settings\Software\Microsoft\Windows\Shell\MuiCache`,
		`Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Compatibility Assistant\Store`,
		`Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers`,
		`Software\Microsoft\Windows\CurrentVersion\Explorer\FeatureUsage\AppSwitched`,
		`Software\Microsoft\Windows\CurrentVersion\Explorer\FeatureUsage\ShowJumpView`,
		`Software\Microsoft\Windows\CurrentVersion\Explorer\FeatureUsage\AppBadgeUpdated`,
		`Software\Microsoft\Windows\CurrentVersion\Explorer\FeatureUsage\AppLaunch`,
	} {
		if k, ok := regOpen(sub, keyReadWrite); ok {
			for _, n := range k.valueNames() {
				if isOurs(n, marks) || containsFold(n, "WoWForeverAddons-Setup") || containsFold(n, `\wfam-uninstall-`) {
					k.deleteValue(n)
				}
			}
			k.close()
		}
	}
	const ua = `Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist`
	if root, ok := regOpen(ua, keyRead); ok {
		for _, g := range root.subKeys() {
			if k, ok := regOpen(ua+`\`+g+`\Count`, keyReadWrite); ok {
				for _, n := range k.valueNames() {
					plain := rot13(n)
					if isOurs(plain, marks) || strings.HasSuffix(strings.ToLower(plain), `\`+strings.ToLower(lnkName)) ||
						containsFold(plain, "WoWForeverAddons-Setup") {
						k.deleteValue(n)
					}
				}
				k.close()
			}
		}
		root.close()
	}

	pr.status("Removing settings and program files...")
	pr.set(750)
	if !deleteBackups {
		zips, _ := filepath.Glob(filepath.Join(p.data, "backups", "WTF-*.zip"))
		if len(zips) > 0 {
			kept = filepath.Join(p.docs, "WoW Forever WTF backups")
			os.MkdirAll(kept, 0755)
			for _, z := range zips {
				t := filepath.Join(kept, filepath.Base(z))
				if os.Rename(z, t) != nil {
					copyFile(z, t)
				}
			}
		}
	}
	removeHard(p.data, 10)
	pr.set(880)
	removeHard(p.dest, 10)
	cleanTemp(self)
	pr.status("Done!")
	pr.set(1000)
	return kept
}

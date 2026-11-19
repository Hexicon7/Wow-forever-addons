// WoW Forever Addons - the desktop program. It runs the manager in the background and shows it in
// its own window (Microsoft Edge WebView2, the same engine Windows itself uses for app windows),
// so there is no web browser involved.
package main

import (
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"syscall"
	"time"
	"unsafe"

	webview2 "github.com/jchv/go-webview2"
	"github.com/jchv/go-webview2/pkg/edge"
	"github.com/jchv/go-webview2/webviewloader"
	"golang.org/x/sys/windows"
)

const (
	title  = "WoW Forever Addons"
	aumid  = "WoWForeverAddons.Manager" // same ID as the shortcuts, so the taskbar groups them
	mutex  = `Local\WoWForeverAddons.DesktopWindow`
	wv2URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
)

var (
	user32  = windows.NewLazySystemDLL("user32.dll")
	shell32 = windows.NewLazySystemDLL("shell32.dll")
	winhttp = windows.NewLazySystemDLL("winhttp.dll")

	procFindWindowW           = user32.NewProc("FindWindowW")
	procShowWindow            = user32.NewProc("ShowWindow")
	procIsIconic              = user32.NewProc("IsIconic")
	procSetForegroundWindow   = user32.NewProc("SetForegroundWindow")
	procMessageBoxW           = user32.NewProc("MessageBoxW")
	procGetWindowPlacement    = user32.NewProc("GetWindowPlacement")
	procSetWindowPlacement    = user32.NewProc("SetWindowPlacement")
	procMonitorFromRect       = user32.NewProc("MonitorFromRect")
	procGetDpiForSystem       = user32.NewProc("GetDpiForSystem")
	procIsWindow              = user32.NewProc("IsWindow")
	procSetAppID              = shell32.NewProc("SetCurrentProcessExplicitAppUserModelID")
	procDwmSetWindowAttribute = windows.NewLazySystemDLL("dwmapi.dll").NewProc("DwmSetWindowAttribute")
	procGetMessageW           = user32.NewProc("GetMessageW")
	procTranslateMessage      = user32.NewProc("TranslateMessage")
	procDispatchMessageW      = user32.NewProc("DispatchMessageW")
	procPostQuitMessage       = user32.NewProc("PostQuitMessage")
	procShellExecuteW         = shell32.NewProc("ShellExecuteW")
)

func init() { runtime.LockOSThread() } // the window lives on the main thread

func u16(s string) *uint16 { p, _ := windows.UTF16PtrFromString(s); return p }

func msgBox(text string, flags uintptr) int {
	r, _, _ := procMessageBoxW.Call(0, uintptr(unsafe.Pointer(u16(text))), uintptr(unsafe.Pointer(u16(title))), flags|0x00010000)
	return int(r)
}

func openInBrowser(url string) {
	procShellExecuteW.Call(0, uintptr(unsafe.Pointer(u16("open"))), uintptr(unsafe.Pointer(u16(url))), 0, 0, 1)
}

func dataDir() string {
	if d := os.Getenv("WFAM_DATA_DIR"); d != "" {
		return d
	}
	return filepath.Join(os.Getenv("APPDATA"), "WoWForeverAddonManager")
}

func main() {
	// DLLs only from System32 and this program's folder (WebView2Loader.dll lives next to the exe).
	windows.SetDefaultDllDirectories(windows.LOAD_LIBRARY_SEARCH_SYSTEM32 | 0x00000200 /*APPLICATION_DIR*/)
	procSetAppID.Call(uintptr(unsafe.Pointer(u16(aumid))))

	exe, err := os.Executable()
	if err != nil {
		msgBox("Couldn't find the program's folder.", 0x10)
		os.Exit(1)
	}
	dir := filepath.Dir(exe)

	startHidden := false
	for _, a := range os.Args[1:] {
		if strings.EqualFold(strings.TrimLeft(a, "-/"), "tray") {
			startHidden = true // started with Windows: just the tray icon
		}
	}
	wmShowMain, _, _ = procRegisterWindowMessageW.Call(uintptr(unsafe.Pointer(u16("WoWForeverAddons.Show"))))

	// Already running? Bring that window to the front instead of opening a second one.
	h, err := windows.CreateMutex(nil, false, u16(mutex))
	if errors.Is(err, windows.ERROR_ALREADY_EXISTS) {
		if !startHidden {
			focusExisting()
		}
		return
	}
	if h != 0 {
		defer windows.CloseHandle(h)
	}

	logf("start %s (args %v)", exe, os.Args[1:])
	v, verr := webviewloader.GetInstalledVersion()
	logf("WebView2 runtime %q err=%v", v, verr)
	if verr != nil || v == "" {
		// Part of Windows 10/11; only missing on stripped-down systems.
		if msgBox("WoW Forever Addons needs the Microsoft WebView2 Runtime, which is part of Windows but "+
			"isn't installed on this PC.\n\nOpen Microsoft's download page for it?", 0x04|0x30) == 6 {
			openInBrowser(wv2URL)
		}
		return
	}

	url, err := startBackend(dir)
	logf("manager at %s err=%v", url, err)
	if err != nil {
		msgBox("The manager couldn't start:\n\n"+err.Error()+"\n\nDetails: "+filepath.Join(dataDir(), "error.log"), 0x10)
		os.Exit(1)
	}
	edge.Fatal = func(msg string) {
		logf("WebView2 failed: %s", msg)
		msgBox("The window couldn't start ("+msg+").\n\nPlease send this file so it can be fixed:\n"+logPath(), 0x10)
		sayBye(base)
		os.Exit(1)
	}
	base = url
	trayOn.Store(true)
	if b, st, err := api("GET", "/api/notices?after=0", ""); err == nil && st == 200 {
		var r struct {
			Tray bool `json:"tray"`
		}
		if json.Unmarshal(b, &r) == nil {
			trayOn.Store(r.Tray)
		}
	}
	initTray()
	onCommand = func(cmd int) {
		switch cmd {
		case cmdOpen:
			showMain()
		case cmdCheck:
			go api("POST", "/api/tray_check", "{}")
		case cmdUpdateAll:
			go api("POST", "/api/tray_update", "{}")
		case cmdQuit:
			quitting.Store(true)
			if mainHwnd != 0 {
				procPostMessageW.Call(mainHwnd, 0x0010, 0, 0) // WM_CLOSE
			} else {
				procPostQuitMessage.Call(0) // the window is parked: just leave the tray loop
			}
		}
	}
	go pollNotices()

	// The window exists while you use it. Hidden in the tray for a while (or started with Windows),
	// it is closed completely to free its memory, and opened again when you click the tray icon.
	parked := startHidden
	for !quitting.Load() {
		if parked {
			if !waitInTray() {
				break
			}
			parked = false
		}
		creating.Store(true)
		w := webview2.NewWithOptions(webview2.WebViewOptions{
			DataPath:  filepath.Join(dataDir(), "webview"),
			AutoFocus: true,
			WindowOptions: webview2.WindowOptions{Title: title, Width: scale(1200), Height: scale(820), IconId: 1, Center: true,
				OnClose: onClose, OnMessage: onMessage,
				OnCreated: func(h uintptr) { darkTitleBar(h); restorePlacement(h, false) }},
		})
		if w == nil {
			removeTrayIcon()
			edge.Fatal("no window")
			return
		}
		w.SetSize(int(scale(880)), int(scale(560)), webview2.HintMin)
		mainHwnd = uintptr(w.Window())
		zoomer, _ = w.(interface{ SetZoom(float64) })
		fitZoom(mainHwnd)
		creating.Store(false)
		logf("window created")
		showMain()
		go watchEmpty(w, url)
		go rememberPlacement(mainHwnd)
		w.Navigate(url)
		w.Run() // returns when the window is gone (closed, or parked to save memory)
		mainHwnd = 0
		zoomer, lastZoom = nil, 0
		if !parking.Load() {
			break // closed for real (tray setting off, or Quit)
		}
		parking.Store(false)
		parked = true
	}
	removeTrayIcon()
	sayBye(url) // the manager finishes any running job, then closes by itself
}

var (
	creating  atomic.Bool
	parking   atomic.Bool
	wantShow  atomic.Bool
	hiddenGen atomic.Int64
)

// waitInTray runs the message loop while there is no window. Returns false to quit.
func waitInTray() bool {
	wantShow.Store(false)
	var m [48]byte
	for {
		r, _, _ := procGetMessageW.Call(uintptr(unsafe.Pointer(&m[0])), 0, 0, 0)
		if int32(r) <= 0 {
			return wantShow.Load() && !quitting.Load()
		}
		procTranslateMessage.Call(uintptr(unsafe.Pointer(&m[0])))
		procDispatchMessageW.Call(uintptr(unsafe.Pointer(&m[0])))
	}
}

// darkTitleBar matches the window frame to the program (Windows 10 20H1+ / Windows 11).
func darkTitleBar(hwnd uintptr) {
	if procDwmSetWindowAttribute.Find() != nil {
		return
	}
	on := uint32(1)
	if r, _, _ := procDwmSetWindowAttribute.Call(hwnd, 20, uintptr(unsafe.Pointer(&on)), 4); r != 0 {
		procDwmSetWindowAttribute.Call(hwnd, 19, uintptr(unsafe.Pointer(&on)), 4) // older Windows 10
	}
	caption, border, text := uint32(0x0014100e), uint32(0x00342a24), uint32(0x00efeae8)
	procDwmSetWindowAttribute.Call(hwnd, 35, uintptr(unsafe.Pointer(&caption)), 4) // Windows 11 only
	procDwmSetWindowAttribute.Call(hwnd, 34, uintptr(unsafe.Pointer(&border)), 4)
	procDwmSetWindowAttribute.Call(hwnd, 36, uintptr(unsafe.Pointer(&text)), 4)
}

// onClose: with the tray setting on, closing only hides the window (like the Wago/CurseForge apps).
func onClose() bool {
	if quitting.Load() || !trayOn.Load() {
		return true
	}
	if parking.Load() {
		return true // closing for real to free memory; the tray icon stays
	}
	procShowWindow.Call(mainHwnd, 0) // SW_HIDE
	// After two minutes in the tray, close the window completely: it comes back on the next click.
	gen := hiddenGen.Add(1)
	hwnd := mainHwnd
	go func() {
		time.Sleep(2 * time.Minute)
		if hiddenGen.Load() == gen && mainHwnd == hwnd && !quitting.Load() {
			if v, _, _ := procIsWindowVisible.Call(hwnd); v == 0 {
				parking.Store(true)
				procPostMessageW.Call(hwnd, 0x0010, 0, 0)
			}
		}
	}()
	hint := filepath.Join(dataDir(), "tray-hint-shown")
	if _, err := os.Stat(hint); err != nil {
		balloon("Still running in the tray", "WoW Forever Addons keeps your addons up to date in the background. "+
			"Right-click the icon to quit, or turn this off in Settings.")
		os.WriteFile(hint, []byte("1"), 0644)
	}
	return false
}

var (
	zoomer              interface{ SetZoom(float64) }
	lastZoom            float64
	procGetClientRect   = user32.NewProc("GetClientRect")
	procGetDpiForWindow = user32.NewProc("GetDpiForWindow")
)

// fitZoom makes the program fill big windows: the wider the window, the larger everything is
// drawn (like browser zoom, in steps of 5 %), so a maximised window on a big screen isn't mostly empty.
func fitZoom(hwnd uintptr) {
	if zoomer == nil || hwnd == 0 {
		return
	}
	var rc [4]int32
	procGetClientRect.Call(hwnd, uintptr(unsafe.Pointer(&rc[0])))
	dpi := uintptr(96)
	if procGetDpiForWindow.Find() == nil {
		if d, _, _ := procGetDpiForWindow.Call(hwnd); d >= 96 {
			dpi = d
		}
	}
	width := float64(rc[2]-rc[0]) * 96 / float64(dpi)
	z := width / 1650
	if z < 1 {
		z = 1
	} else if z > 1.35 {
		z = 1.35
	}
	z = float64(int(z*20+0.5)) / 20
	if z != lastZoom && width > 0 {
		lastZoom = z
		zoomer.SetZoom(z)
	}
}

func onMessage(hwnd, msg, wp, lp uintptr) (uintptr, bool) {
	if msg == 0x0005 && wp != 1 { // WM_SIZE (not minimised)
		fitZoom(hwnd)
	}
	if wmShowMain != 0 && msg == wmShowMain { // another start of the program asked for the window
		showMain()
		return 0, true
	}
	return 0, false
}

// pollNotices shows the manager's notifications (updates found/installed) while the window
// isn't in front, and keeps the tray setting in sync.
func pollNotices() {
	started := float64(time.Now().Unix()) - 5
	after := 0
	for !quitting.Load() {
		body, st, err := api("GET", fmt.Sprintf("/api/notices?after=%d", after), "")
		if err == nil && st == 200 {
			var r struct {
				Notices []struct {
					ID    int     `json:"id"`
					T     float64 `json:"t"`
					Title string  `json:"title"`
					Text  string  `json:"text"`
				} `json:"notices"`
				Last int  `json:"last"`
				Tray bool `json:"tray"`
				Busy bool `json:"busy"`
			}
			if json.Unmarshal(body, &r) == nil {
				trayOn.Store(r.Tray)
				busy.Store(r.Busy)
				for _, n := range r.Notices {
					if n.T >= started && !windowInView() {
						balloon(n.Title, n.Text)
					}
				}
				if r.Last > after {
					after = r.Last
				}
			}
		}
		time.Sleep(3 * time.Second)
	}
}

func logPath() string { return filepath.Join(dataDir(), "desktop.log") }

// logf keeps a short log of the window's start-up, for troubleshooting.
func logf(format string, a ...interface{}) {
	p := logPath()
	if st, err := os.Stat(p); err == nil && st.Size() > 256<<10 {
		os.Remove(p)
	}
	os.MkdirAll(dataDir(), 0755)
	if f, err := os.OpenFile(p, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644); err == nil {
		fmt.Fprintf(f, "%s  %s\r\n", time.Now().Format("2006-01-02 15:04:05"), fmt.Sprintf(format, a...))
		f.Close()
	}
}

// watchEmpty: if the page never talks to the manager, load it again once; then explain.
func watchEmpty(w webview2.WebView, url string) {
	hwnd := mainHwnd
	for i := 0; i < 40; i++ {
		time.Sleep(time.Second)
		if mainHwnd != hwnd || quitting.Load() {
			return
		}
		if b, st, err := api("GET", "/api/notices?after=999999999", ""); err == nil && st == 200 {
			var r struct {
				UI float64 `json:"ui_age"`
			}
			if json.Unmarshal(b, &r) == nil && r.UI >= 0 && r.UI < 30 {
				logf("page is showing (after %d s)", i+1)
				return
			}
		}
		if i == 12 {
			logf("page not showing after 12 s: loading it again")
			w.Dispatch(func() { w.Navigate(url) })
		}
	}
	logf("the window stayed empty")
	msgBox("The window stayed empty.\n\nPlease send this file so it can be fixed:\n"+logPath(), 0x30)
}

func scale(v uint) uint {
	dpi := uintptr(96)
	if procGetDpiForSystem.Find() == nil {
		if d, _, _ := procGetDpiForSystem.Call(); d >= 96 {
			dpi = d
		}
	}
	return v * uint(dpi) / 96
}

func focusExisting() {
	for i := 0; i < 20; i++ {
		hwnd, _, _ := procFindWindowW.Call(uintptr(unsafe.Pointer(u16("WoWForeverAddonsTray"))), 0)
		if hwnd == 0 {
			hwnd, _, _ = procFindWindowW.Call(uintptr(unsafe.Pointer(u16("webview"))), uintptr(unsafe.Pointer(u16(title))))
		}
		if hwnd != 0 {
			var pid uint32
			procGetWindowThreadProcessId.Call(hwnd, uintptr(unsafe.Pointer(&pid)))
			procAllowSetForegroundWindow.Call(uintptr(pid)) // we were just started by you: hand over the focus
			procPostMessageW.Call(hwnd, wmShowMain, 0, 0)
			return
		}
		time.Sleep(250 * time.Millisecond) // it may still be starting
	}
}

func backendCmd(dir string, args ...string) *exec.Cmd {
	cmd := exec.Command(filepath.Join(dir, "python", "pythonw.exe"), append([]string{filepath.Join(dir, "WoWForeverAddons.pyw")}, args...)...)
	cmd.Dir = dir
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000}
	return cmd
}

// startBackend runs the manager without a browser and waits until it tells us its address.
func startBackend(dir string) (string, error) {
	ready := filepath.Join(os.TempDir(), fmt.Sprintf("wfam-ready-%d.txt", os.Getpid()))
	os.Remove(ready)
	defer os.Remove(ready)
	cmd := backendCmd(dir, "--no-browser", "--ready-file", ready, "--host-pid", fmt.Sprint(os.Getpid()))
	if err := cmd.Start(); err != nil {
		return "", err
	}
	exited := make(chan struct{})
	go func() { cmd.Wait(); close(exited) }()
	deadline := time.Now().Add(60 * time.Second)
	for time.Now().Before(deadline) {
		if b, err := os.ReadFile(ready); err == nil && strings.HasPrefix(string(b), "http://127.0.0.1:") {
			return strings.TrimSpace(string(b)), nil
		}
		select {
		case <-exited:
			if b, err := os.ReadFile(ready); err == nil && strings.HasPrefix(string(b), "http://127.0.0.1:") {
				return strings.TrimSpace(string(b)), nil // it handed over to a copy that was already running
			}
			return "", errors.New("it stopped right after starting")
		case <-time.After(100 * time.Millisecond):
		}
	}
	return "", errors.New("it didn't answer within a minute")
}

// --------------------------------------------------------------------------- window position

type winPlacement struct {
	length, flags, showCmd   uint32
	minX, minY, maxX, maxY   int32
	left, top, right, bottom int32
}

func placementFile() string { return filepath.Join(dataDir(), "desktop-window.dat") }

func restorePlacement(hwnd uintptr, hidden bool) {
	b, err := os.ReadFile(placementFile())
	if err != nil {
		return
	}
	raw, err := hex.DecodeString(strings.TrimSpace(string(b)))
	var wp winPlacement
	if err != nil || len(raw) != int(unsafe.Sizeof(wp)) {
		return
	}
	copy(unsafe.Slice((*byte)(unsafe.Pointer(&wp)), len(raw)), raw)
	wp.length = uint32(unsafe.Sizeof(wp))
	if wp.right-wp.left < 300 || wp.bottom-wp.top < 200 {
		return
	}
	if m, _, _ := procMonitorFromRect.Call(uintptr(unsafe.Pointer(&wp.left)), 0); m == 0 {
		return // that screen isn't connected any more
	}
	if wp.showCmd != 3 { // keep maximized, otherwise show normally (never start minimized)
		wp.showCmd = 1
	}
	if hidden {
		wp.showCmd = 0 // SW_HIDE: started in the tray
	}
	procSetWindowPlacement.Call(hwnd, uintptr(unsafe.Pointer(&wp)))
}

func rememberPlacement(hwnd uintptr) {
	last := ""
	for {
		time.Sleep(2 * time.Second)
		if r, _, _ := procIsWindow.Call(hwnd); r == 0 {
			return
		}
		var wp winPlacement
		wp.length = uint32(unsafe.Sizeof(wp))
		if r, _, _ := procGetWindowPlacement.Call(hwnd, uintptr(unsafe.Pointer(&wp))); r == 0 || wp.showCmd == 2 {
			continue
		}
		s := hex.EncodeToString(unsafe.Slice((*byte)(unsafe.Pointer(&wp)), unsafe.Sizeof(wp)))
		if s != last {
			os.MkdirAll(dataDir(), 0755)
			if os.WriteFile(placementFile(), []byte(s), 0644) == nil {
				last = s
			}
		}
	}
}

// --------------------------------------------------------------------------- closing

var (
	procWinHttpOpen            = winhttp.NewProc("WinHttpOpen")
	procWinHttpSetTimeouts     = winhttp.NewProc("WinHttpSetTimeouts")
	procWinHttpConnect         = winhttp.NewProc("WinHttpConnect")
	procWinHttpOpenRequest     = winhttp.NewProc("WinHttpOpenRequest")
	procWinHttpSendRequest     = winhttp.NewProc("WinHttpSendRequest")
	procWinHttpReceiveResponse = winhttp.NewProc("WinHttpReceiveResponse")
	procWinHttpQueryHeaders    = winhttp.NewProc("WinHttpQueryHeaders")
	procWinHttpReadData        = winhttp.NewProc("WinHttpReadData")
	procWinHttpCloseHandle     = winhttp.NewProc("WinHttpCloseHandle")
)

var base string // the manager's address, e.g. http://127.0.0.1:47811/

// api talks to the manager on this computer (never through a proxy).
func api(method, path, body string) ([]byte, int, error) {
	hostport := strings.SplitN(strings.TrimPrefix(base, "http://"), "/", 2)[0]
	i := strings.LastIndexByte(hostport, ':')
	if i <= 0 {
		return nil, 0, errors.New("no address")
	}
	var port int
	fmt.Sscanf(hostport[i+1:], "%d", &port)
	ses, _, _ := procWinHttpOpen.Call(uintptr(unsafe.Pointer(u16("WoWForeverAddons"))), 1 /*NO_PROXY*/, 0, 0, 0)
	if ses == 0 {
		return nil, 0, errors.New("WinHTTP unavailable")
	}
	defer procWinHttpCloseHandle.Call(ses)
	procWinHttpSetTimeouts.Call(ses, 3000, 3000, 5000, 5000)
	conn, _, _ := procWinHttpConnect.Call(ses, uintptr(unsafe.Pointer(u16(hostport[:i]))), uintptr(port), 0)
	if conn == 0 {
		return nil, 0, errors.New("can't connect")
	}
	defer procWinHttpCloseHandle.Call(conn)
	req, _, _ := procWinHttpOpenRequest.Call(conn, uintptr(unsafe.Pointer(u16(method))), uintptr(unsafe.Pointer(u16(path))), 0, 0, 0, 0)
	if req == 0 {
		return nil, 0, errors.New("can't open request")
	}
	defer procWinHttpCloseHandle.Call(req)
	hdr := "X-WFAM: 1\r\nContent-Type: application/json\r\n"
	if path == "/api/bye" {
		hdr = "Origin: http://" + hostport + "\r\nContent-Type: text/plain\r\n"
	}
	var bp uintptr
	b := []byte(body)
	if len(b) > 0 {
		bp = uintptr(unsafe.Pointer(&b[0]))
	}
	if r, _, _ := procWinHttpSendRequest.Call(req, uintptr(unsafe.Pointer(u16(hdr))), 0xFFFFFFFF, bp, uintptr(len(b)), uintptr(len(b)), 0); r == 0 {
		return nil, 0, errors.New("send failed")
	}
	if r, _, _ := procWinHttpReceiveResponse.Call(req, 0); r == 0 {
		return nil, 0, errors.New("no answer")
	}
	var status, size uint32 = 0, 4
	procWinHttpQueryHeaders.Call(req, 19|0x20000000, 0, uintptr(unsafe.Pointer(&status)), uintptr(unsafe.Pointer(&size)), 0)
	var out []byte
	buf := make([]byte, 16*1024)
	for len(out) < 4<<20 {
		var n uint32
		if r, _, _ := procWinHttpReadData.Call(req, uintptr(unsafe.Pointer(&buf[0])), uintptr(len(buf)), uintptr(unsafe.Pointer(&n))); r == 0 || n == 0 {
			break
		}
		out = append(out, buf[:n]...)
	}
	return out, int(status), nil
}

// sayBye tells the manager its window closed (same as the page does when it unloads).
func sayBye(url string) {
	base = url
	api("POST", "/api/bye", "")
}

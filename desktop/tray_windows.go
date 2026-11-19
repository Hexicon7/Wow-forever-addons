package main

// System tray icon: lives while the program runs. Left-click opens the window, right-click shows
// a small menu, and notifications from the manager (updates found or installed) appear as
// Windows notifications.

import (
	"sync/atomic"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

var (
	kernel32                     = windows.NewLazySystemDLL("kernel32.dll")
	procGetModuleHandleW         = kernel32.NewProc("GetModuleHandleW")
	procRegisterClassExW         = user32.NewProc("RegisterClassExW")
	procCreateWindowExW          = user32.NewProc("CreateWindowExW")
	procDefWindowProcW           = user32.NewProc("DefWindowProcW")
	procRegisterWindowMessageW   = user32.NewProc("RegisterWindowMessageW")
	procCreatePopupMenu          = user32.NewProc("CreatePopupMenu")
	procAppendMenuW              = user32.NewProc("AppendMenuW")
	procTrackPopupMenu           = user32.NewProc("TrackPopupMenu")
	procDestroyMenu              = user32.NewProc("DestroyMenu")
	procSetMenuDefaultItem       = user32.NewProc("SetMenuDefaultItem")
	procGetCursorPos             = user32.NewProc("GetCursorPos")
	procPostMessageW             = user32.NewProc("PostMessageW")
	procLoadImageW               = user32.NewProc("LoadImageW")
	procGetSystemMetrics         = user32.NewProc("GetSystemMetrics")
	procIsWindowVisible          = user32.NewProc("IsWindowVisible")
	procGetForegroundWindow      = user32.NewProc("GetForegroundWindow")
	procAllowSetForegroundWindow = user32.NewProc("AllowSetForegroundWindow")
	procGetWindowThreadProcessId = user32.NewProc("GetWindowThreadProcessId")
	procShellNotifyIconW         = shell32.NewProc("Shell_NotifyIconW")
)

const (
	wmTray       = 0x8000 + 10 // WM_APP+10: messages from the tray icon
	nimAdd       = 0
	nimModify    = 1
	nimDelete    = 2
	nimSetVer    = 4
	nifMessage   = 0x01
	nifIcon      = 0x02
	nifTip       = 0x04
	nifInfo      = 0x10
	cmdOpen      = 1
	cmdCheck     = 2
	cmdUpdateAll = 3
	cmdQuit      = 4
)

type notifyIconData struct {
	cbSize           uint32
	hWnd             uintptr
	uID              uint32
	uFlags           uint32
	uCallbackMessage uint32
	hIcon            uintptr
	szTip            [128]uint16
	dwState          uint32
	dwStateMask      uint32
	szInfo           [256]uint16
	uVersion         uint32
	szInfoTitle      [64]uint16
	dwInfoFlags      uint32
	guidItem         [16]byte
	hBalloonIcon     uintptr
}

type wndClassEx struct {
	size       uint32
	style      uint32
	wndProc    uintptr
	clsExtra   int32
	wndExtra   int32
	instance   uintptr
	icon       uintptr
	cursor     uintptr
	background uintptr
	menuName   *uint16
	className  *uint16
	iconSm     uintptr
}

var (
	trayHwnd         uintptr
	mainHwnd         uintptr
	smallIcon        uintptr
	bigIcon          uintptr
	wmTaskbarCreated uintptr
	wmShowMain       uintptr
	trayOn           atomic.Bool // "keep running in the tray" setting (from the manager)
	busy             atomic.Bool // the manager is working on something
	quitting         atomic.Bool
	lastClick        time.Time
	onCommand        func(cmd int)
)

func setText(dst []uint16, s string) {
	u, _ := windows.UTF16FromString(s)
	if len(u) > len(dst) {
		u = u[:len(dst)]
		u[len(u)-1] = 0
	}
	copy(dst, u)
}

func baseNID() notifyIconData {
	n := notifyIconData{hWnd: trayHwnd, uID: 1}
	n.cbSize = uint32(unsafe.Sizeof(n))
	return n
}

func addTrayIcon() {
	n := baseNID()
	n.uFlags = nifMessage | nifIcon | nifTip
	n.uCallbackMessage = wmTray
	n.hIcon = smallIcon
	setText(n.szTip[:], title)
	procShellNotifyIconW.Call(nimAdd, uintptr(unsafe.Pointer(&n)))
	n.uVersion = 3 // NOTIFYICON_VERSION: also report balloon clicks
	procShellNotifyIconW.Call(nimSetVer, uintptr(unsafe.Pointer(&n)))
}

func removeTrayIcon() {
	n := baseNID()
	procShellNotifyIconW.Call(nimDelete, uintptr(unsafe.Pointer(&n)))
}

func balloon(head, text string) {
	n := baseNID()
	n.uFlags = nifInfo
	setText(n.szInfoTitle[:], head)
	setText(n.szInfo[:], text)
	n.dwInfoFlags = 0x04 | 0x20 | 0x80 // NIIF_USER | NIIF_LARGE_ICON | NIIF_RESPECT_QUIET_TIME
	n.hBalloonIcon = bigIcon
	procShellNotifyIconW.Call(nimModify, uintptr(unsafe.Pointer(&n)))
}

func showMain() {
	hiddenGen.Add(1) // cancels a pending "free the memory" timer
	if creating.Load() {
		return // it is opening right now
	}
	if mainHwnd == 0 { // the window was closed to save memory: open it again
		if !wantShow.Load() {
			wantShow.Store(true)
			procPostQuitMessage.Call(0)
		}
		return
	}
	procShowWindow.Call(mainHwnd, 5) // SW_SHOW
	if r, _, _ := procIsIconic.Call(mainHwnd); r != 0 {
		procShowWindow.Call(mainHwnd, 9) // SW_RESTORE
	}
	procSetForegroundWindow.Call(mainHwnd)
}

// windowInView: the user is looking at the window, so the log there is enough (no notification).
func windowInView() bool {
	if mainHwnd == 0 {
		return false
	}
	v, _, _ := procIsWindowVisible.Call(mainHwnd)
	i, _, _ := procIsIconic.Call(mainHwnd)
	f, _, _ := procGetForegroundWindow.Call()
	return v != 0 && i == 0 && f == mainHwnd
}

func showMenu(hwnd uintptr) {
	m, _, _ := procCreatePopupMenu.Call()
	if m == 0 {
		return
	}
	defer procDestroyMenu.Call(m)
	grey := uintptr(0)
	if busy.Load() {
		grey = 0x1 // MF_GRAYED
	}
	procAppendMenuW.Call(m, 0, cmdOpen, uintptr(unsafe.Pointer(u16("Open WoW Forever Addons"))))
	procAppendMenuW.Call(m, grey, cmdCheck, uintptr(unsafe.Pointer(u16("Check for updates now"))))
	procAppendMenuW.Call(m, grey, cmdUpdateAll, uintptr(unsafe.Pointer(u16("Update all addons"))))
	procAppendMenuW.Call(m, 0x800, 0, 0) // separator
	procAppendMenuW.Call(m, 0, cmdQuit, uintptr(unsafe.Pointer(u16("Quit"))))
	procSetMenuDefaultItem.Call(m, cmdOpen, 0)
	var pt [2]int32
	procGetCursorPos.Call(uintptr(unsafe.Pointer(&pt[0])))
	procSetForegroundWindow.Call(hwnd) // so the menu closes when you click elsewhere
	cmd, _, _ := procTrackPopupMenu.Call(m, 0x0100|0x0002|0x0080, uintptr(pt[0]), uintptr(pt[1]), 0, hwnd, 0)
	procPostMessageW.Call(hwnd, 0, 0, 0)
	if cmd != 0 && onCommand != nil {
		onCommand(int(cmd))
	}
}

func trayWndProc(hwnd, msg, wp, lp uintptr) uintptr {
	if msg == wmTray {
		switch lp & 0xFFFF {
		case 0x0202, 0x0203, 0x0400, 0x0401, 0x0405: // L-button up/double, NIN_SELECT/KEYSELECT, balloon click
			if time.Since(lastClick) > 300*time.Millisecond {
				lastClick = time.Now()
				showMain()
			}
		case 0x0205, 0x007B: // R-button up, WM_CONTEXTMENU
			if time.Since(lastClick) > 300*time.Millisecond {
				lastClick = time.Now()
				showMenu(hwnd)
			}
		}
		return 0
	}
	if wmShowMain != 0 && msg == wmShowMain { // another start of the program asked for the window
		showMain()
		return 0
	}
	if wmTaskbarCreated != 0 && msg == wmTaskbarCreated { // Explorer restarted: put the icon back
		addTrayIcon()
		return 0
	}
	r, _, _ := procDefWindowProcW.Call(hwnd, msg, wp, lp)
	return r
}

func initTray() {
	hinst, _, _ := procGetModuleHandleW.Call(0)
	cx, _, _ := procGetSystemMetrics.Call(49) // SM_CXSMICON
	cy, _, _ := procGetSystemMetrics.Call(50)
	smallIcon, _, _ = procLoadImageW.Call(hinst, 1, 1, cx, cy, 0)
	bigIcon, _, _ = procLoadImageW.Call(hinst, 1, 1, 0, 0, 0x40) // LR_DEFAULTSIZE
	wmTaskbarCreated, _, _ = procRegisterWindowMessageW.Call(uintptr(unsafe.Pointer(u16("TaskbarCreated"))))
	cls := u16("WoWForeverAddonsTray")
	wc := wndClassEx{wndProc: windows.NewCallback(trayWndProc), instance: hinst, className: cls}
	wc.size = uint32(unsafe.Sizeof(wc))
	procRegisterClassExW.Call(uintptr(unsafe.Pointer(&wc)))
	trayHwnd, _, _ = procCreateWindowExW.Call(0, uintptr(unsafe.Pointer(cls)), uintptr(unsafe.Pointer(u16("WoWForeverAddonsTray"))),
		0, 0, 0, 0, 0, 0, 0, hinst, 0)
	if trayHwnd != 0 {
		addTrayIcon()
	}
}

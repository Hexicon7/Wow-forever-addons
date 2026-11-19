package main

import (
	"errors"
	"sync/atomic"
	"syscall"
	"unsafe"
)

const (
	wmDestroy         = 0x0002
	wmSetText         = 0x000C
	wmClose           = 0x0010
	wmSetFont         = 0x0030
	wmCtlColorStatic  = 0x0138
	wmAppDone         = 0x8001
	pbmSetPos         = 0x0402
	pbmSetRange32     = 0x0406
	wsChildVisible    = 0x50000000
	wsMainWindow      = 0x00C00000 | 0x00080000 | 0x00020000 // CAPTION | SYSMENU | MINIMIZEBOX
	mbYesNo           = 0x04
	mbIconError       = 0x10
	mbIconQuestion    = 0x20
	mbIconInformation = 0x40
	mbTopmostFG       = 0x00040000 | 0x00010000 // TOPMOST | SETFOREGROUND
	idYes             = 6
)

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

type winMsg struct {
	hwnd    uintptr
	message uint32
	wParam  uintptr
	lParam  uintptr
	time    uint32
	ptX     int32
	ptY     int32
	private uint32
}

type winRect struct{ left, top, right, bottom int32 }

func msgBox(text string, flags uintptr) int {
	r, _, _ := procMessageBoxW.Call(0, uintptr(unsafe.Pointer(u16(text))), uintptr(unsafe.Pointer(u16(appTitle))), flags|mbTopmostFG)
	return int(r)
}

func ask(text string) bool { return msgBox(text, mbYesNo|mbIconQuestion) == idYes }

// progress is how the work reports back; with no window it does nothing.
type progress struct {
	label, bar uintptr
}

func (p *progress) status(s string) {
	if p != nil && p.label != 0 {
		procSendMessageW.Call(p.label, wmSetText, 0, uintptr(unsafe.Pointer(u16(s))))
	}
}

func (p *progress) set(permille int) {
	if p != nil && p.bar != 0 {
		procSendMessageW.Call(p.bar, pbmSetPos, uintptr(permille), 0)
	}
}

var working atomic.Bool

func wndProc(hwnd, msg, wp, lp uintptr) uintptr {
	switch msg {
	case wmClose:
		if working.Load() {
			return 0 // don't stop half-way
		}
	case wmAppDone:
		procDestroyWindow.Call(hwnd)
		return 0
	case wmDestroy:
		procPostQuitMessage.Call(0)
		return 0
	case wmCtlColorStatic:
		procSetBkMode.Call(wp, 1)               // TRANSPARENT
		r, _, _ := procGetSysColorBrush.Call(5) // COLOR_WINDOW
		return r
	}
	r, _, _ := procDefWindowProcW.Call(hwnd, msg, wp, lp)
	return r
}

var errNoWindow = errors.New("no window")

// withWindow shows a small progress window and runs work on another goroutine.
// The calling goroutine must be the main (locked) thread.
func withWindow(title, first string, work func(p *progress) error) error {
	hinst, _, _ := procGetModuleHandleW.Call(0)
	icc := [2]uint32{8, 0x20} // ICC_PROGRESS_CLASS
	procInitCommonControlsEx.Call(uintptr(unsafe.Pointer(&icc[0])))
	cls := u16("WFAMSetupWindow")
	icon, _, _ := procLoadIconW.Call(hinst, 1)
	cursor, _, _ := procLoadCursorW.Call(0, 32512)
	wc := wndClassEx{wndProc: syscall.NewCallback(wndProc), instance: hinst, icon: icon, iconSm: icon,
		cursor: cursor, background: 6 /*COLOR_WINDOW+1*/, className: cls}
	wc.size = uint32(unsafe.Sizeof(wc))
	if r, _, _ := procRegisterClassExW.Call(uintptr(unsafe.Pointer(&wc))); r == 0 {
		return errNoWindow
	}
	dc, _, _ := procGetDC.Call(0)
	dpiR, _, _ := procGetDeviceCaps.Call(dc, 90) // LOGPIXELSY
	procReleaseDC.Call(0, dc)
	dpi := int(dpiR)
	if dpi < 96 {
		dpi = 96
	}
	s := func(v int) int { return v * dpi / 96 }
	rc := winRect{0, 0, int32(s(470)), int32(s(128))}
	procAdjustWindowRectEx.Call(uintptr(unsafe.Pointer(&rc)), wsMainWindow, 0, 0)
	w, h := int(rc.right-rc.left), int(rc.bottom-rc.top)
	sw, _, _ := procGetSystemMetrics.Call(0)
	sh, _, _ := procGetSystemMetrics.Call(1)
	x, y := (int(sw)-w)/2, (int(sh)-h)/2
	hwnd, _, _ := procCreateWindowExW.Call(0, uintptr(unsafe.Pointer(cls)), uintptr(unsafe.Pointer(u16(title))),
		wsMainWindow, uintptr(x), uintptr(y), uintptr(w), uintptr(h), 0, 0, hinst, 0)
	if hwnd == 0 {
		return errNoWindow
	}
	font, _, _ := procCreateFontW.Call(uintptr(-s(12)), 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0,
		uintptr(unsafe.Pointer(u16("Segoe UI"))))
	label, _, _ := procCreateWindowExW.Call(0, uintptr(unsafe.Pointer(u16("STATIC"))), uintptr(unsafe.Pointer(u16(first))),
		wsChildVisible, uintptr(s(22)), uintptr(s(20)), uintptr(s(426)), uintptr(s(52)), hwnd, 0, hinst, 0)
	bar, _, _ := procCreateWindowExW.Call(0, uintptr(unsafe.Pointer(u16("msctls_progress32"))), 0,
		wsChildVisible, uintptr(s(22)), uintptr(s(82)), uintptr(s(426)), uintptr(s(18)), hwnd, 0, hinst, 0)
	if font != 0 {
		procSendMessageW.Call(label, wmSetFont, font, 1)
	}
	procSendMessageW.Call(bar, pbmSetRange32, 0, 1000)
	procShowWindow.Call(hwnd, 5)
	procUpdateWindow.Call(hwnd)
	procSetForegroundWindow.Call(hwnd)

	var werr error
	working.Store(true)
	go func() {
		defer func() {
			if r := recover(); r != nil {
				werr = errors.New("unexpected error in the installer")
			}
			working.Store(false)
			procPostMessageW.Call(hwnd, wmAppDone, 0, 0)
		}()
		werr = work(&progress{label: label, bar: bar})
	}()
	var m winMsg
	for {
		r, _, _ := procGetMessageW.Call(uintptr(unsafe.Pointer(&m)), 0, 0, 0)
		if int32(r) <= 0 {
			break
		}
		procTranslateMessage.Call(uintptr(unsafe.Pointer(&m)))
		procDispatchMessageW.Call(uintptr(unsafe.Pointer(&m)))
	}
	for working.Load() { // only if the loop ended early
		procGetMessageW.Call(uintptr(unsafe.Pointer(&m)), 0, 0, 0)
	}
	return werr
}

// run shows the window unless quiet; if a window can't be made, the work still runs.
func run(quiet bool, title, first string, work func(p *progress) error) error {
	if quiet {
		return work(nil)
	}
	err := withWindow(title, first, work)
	if err == errNoWindow {
		return work(nil)
	}
	return err
}

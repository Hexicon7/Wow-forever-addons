package main

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"time"
	"unicode/utf16"
	"unsafe"
)

// ---------------------------------------------------------------------------
// DLLs are always loaded from System32 by full path (no DLL planting from Downloads).
// ---------------------------------------------------------------------------

var kernel32 = syscall.NewLazyDLL("kernel32.dll") // a KnownDLL: always the system copy

var (
	procGetSystemDirectoryW        = kernel32.NewProc("GetSystemDirectoryW")
	procSetDefaultDllDirectories   = kernel32.NewProc("SetDefaultDllDirectories")
	procQueryFullProcessImageNameW = kernel32.NewProc("QueryFullProcessImageNameW")
	procIsWow64Process2            = kernel32.NewProc("IsWow64Process2")
	procGetCurrentProcess          = kernel32.NewProc("GetCurrentProcess")
	procGetModuleHandleW           = kernel32.NewProc("GetModuleHandleW")
)

var sys32 string

func systemDir() string {
	buf := make([]uint16, 520)
	n, _, _ := procGetSystemDirectoryW.Call(uintptr(unsafe.Pointer(&buf[0])), uintptr(len(buf)))
	if n == 0 || int(n) > len(buf) {
		return `C:\Windows\System32`
	}
	return syscall.UTF16ToString(buf[:n])
}

func sysDLL(name string) *syscall.LazyDLL { return syscall.NewLazyDLL(filepath.Join(sys32, name)) }

var (
	user32, gdi32, advapi32, shell32, ole32, comctl32, wintrust, crypt32, ntdll *syscall.LazyDLL

	procRegisterClassExW, procCreateWindowExW, procDefWindowProcW, procDestroyWindow, procPostQuitMessage,
	procGetMessageW, procTranslateMessage, procDispatchMessageW, procSendMessageW, procPostMessageW,
	procMessageBoxW, procLoadIconW, procLoadCursorW, procGetSystemMetrics, procShowWindow, procUpdateWindow,
	procGetDC, procReleaseDC, procSetForegroundWindow, procGetSysColorBrush, procAdjustWindowRectEx,
	procCreateFontW, procGetDeviceCaps, procSetBkMode,
	procRegCreateKeyExW, procRegSetValueExW, procRegDeleteTreeW, procRegDeleteKeyW, procRegOpenKeyExW,
	procRegEnumValueW, procRegDeleteValueW, procRegEnumKeyExW, procRegCloseKey, procRegQueryValueExW,
	procSHGetFolderPathW, procSHChangeNotify,
	procCoInitializeEx, procCoCreateInstance, procCoUninitialize,
	procInitCommonControlsEx,
	procWinVerifyTrust, procWTHelperProvDataFromStateData, procWTHelperGetProvSignerFromChain, procWTHelperGetProvCertFromChain,
	procCertGetNameStringW,
	procNtQueryInformationProcess *syscall.LazyProc
)

func initAPI() {
	// LOAD_LIBRARY_SEARCH_SYSTEM32: anything loaded later (also indirectly) comes from System32 only.
	procSetDefaultDllDirectories.Call(0x00000800)
	sys32 = systemDir()
	user32, gdi32, advapi32 = sysDLL("user32.dll"), sysDLL("gdi32.dll"), sysDLL("advapi32.dll")
	shell32, ole32, comctl32 = sysDLL("shell32.dll"), sysDLL("ole32.dll"), sysDLL("comctl32.dll")
	wintrust, crypt32, ntdll = sysDLL("wintrust.dll"), sysDLL("crypt32.dll"), sysDLL("ntdll.dll")

	procRegisterClassExW = user32.NewProc("RegisterClassExW")
	procCreateWindowExW = user32.NewProc("CreateWindowExW")
	procDefWindowProcW = user32.NewProc("DefWindowProcW")
	procDestroyWindow = user32.NewProc("DestroyWindow")
	procPostQuitMessage = user32.NewProc("PostQuitMessage")
	procGetMessageW = user32.NewProc("GetMessageW")
	procTranslateMessage = user32.NewProc("TranslateMessage")
	procDispatchMessageW = user32.NewProc("DispatchMessageW")
	procSendMessageW = user32.NewProc("SendMessageW")
	procPostMessageW = user32.NewProc("PostMessageW")
	procMessageBoxW = user32.NewProc("MessageBoxW")
	procLoadIconW = user32.NewProc("LoadIconW")
	procLoadCursorW = user32.NewProc("LoadCursorW")
	procGetSystemMetrics = user32.NewProc("GetSystemMetrics")
	procShowWindow = user32.NewProc("ShowWindow")
	procUpdateWindow = user32.NewProc("UpdateWindow")
	procGetDC = user32.NewProc("GetDC")
	procReleaseDC = user32.NewProc("ReleaseDC")
	procSetForegroundWindow = user32.NewProc("SetForegroundWindow")
	procGetSysColorBrush = user32.NewProc("GetSysColorBrush")
	procAdjustWindowRectEx = user32.NewProc("AdjustWindowRectEx")
	procCreateFontW = gdi32.NewProc("CreateFontW")
	procGetDeviceCaps = gdi32.NewProc("GetDeviceCaps")
	procSetBkMode = gdi32.NewProc("SetBkMode")
	procRegCreateKeyExW = advapi32.NewProc("RegCreateKeyExW")
	procRegSetValueExW = advapi32.NewProc("RegSetValueExW")
	procRegDeleteTreeW = advapi32.NewProc("RegDeleteTreeW")
	procRegDeleteKeyW = advapi32.NewProc("RegDeleteKeyW")
	procRegOpenKeyExW = advapi32.NewProc("RegOpenKeyExW")
	procRegEnumValueW = advapi32.NewProc("RegEnumValueW")
	procRegDeleteValueW = advapi32.NewProc("RegDeleteValueW")
	procRegEnumKeyExW = advapi32.NewProc("RegEnumKeyExW")
	procRegCloseKey = advapi32.NewProc("RegCloseKey")
	procRegQueryValueExW = advapi32.NewProc("RegQueryValueExW")
	procSHGetFolderPathW = shell32.NewProc("SHGetFolderPathW")
	procSHChangeNotify = shell32.NewProc("SHChangeNotify")
	procCoInitializeEx = ole32.NewProc("CoInitializeEx")
	procCoCreateInstance = ole32.NewProc("CoCreateInstance")
	procCoUninitialize = ole32.NewProc("CoUninitialize")
	procInitCommonControlsEx = comctl32.NewProc("InitCommonControlsEx")
	procWinVerifyTrust = wintrust.NewProc("WinVerifyTrust")
	procWTHelperProvDataFromStateData = wintrust.NewProc("WTHelperProvDataFromStateData")
	procWTHelperGetProvSignerFromChain = wintrust.NewProc("WTHelperGetProvSignerFromChain")
	procWTHelperGetProvCertFromChain = wintrust.NewProc("WTHelperGetProvCertFromChain")
	procCertGetNameStringW = crypt32.NewProc("CertGetNameStringW")
	procNtQueryInformationProcess = ntdll.NewProc("NtQueryInformationProcess")
}

func u16(s string) *uint16 {
	p, err := syscall.UTF16PtrFromString(s)
	if err != nil {
		p, _ = syscall.UTF16PtrFromString("")
	}
	return p
}

// ---------------------------------------------------------------------------
// Folders
// ---------------------------------------------------------------------------

const (
	csidlPrograms     = 0x02
	csidlPersonal     = 0x05
	csidlDesktopDir   = 0x10
	csidlAppData      = 0x1a
	csidlLocalAppData = 0x1c
)

func folder(csidl int, fallbackEnv, fallbackSub string) string {
	buf := make([]uint16, 520)
	r, _, _ := procSHGetFolderPathW.Call(0, uintptr(csidl), 0, 0, uintptr(unsafe.Pointer(&buf[0])))
	if r == 0 {
		if s := syscall.UTF16ToString(buf); s != "" {
			return s
		}
	}
	return filepath.Join(os.Getenv(fallbackEnv), fallbackSub)
}

func nativeArch() string {
	var pm, nm uint16
	if procIsWow64Process2.Find() == nil {
		cur, _, _ := procGetCurrentProcess.Call()
		r, _, _ := procIsWow64Process2.Call(cur, uintptr(unsafe.Pointer(&pm)), uintptr(unsafe.Pointer(&nm)))
		if r == 0 {
			nm = 0
		}
	}
	env := os.Getenv("PROCESSOR_ARCHITEW6432")
	if env == "" {
		env = os.Getenv("PROCESSOR_ARCHITECTURE")
	}
	return pythonArch(nm, env)
}

// ---------------------------------------------------------------------------
// Registry (HKEY_CURRENT_USER only)
// ---------------------------------------------------------------------------

const (
	hkcu         = uintptr(0x80000001)
	keyRead      = 0x20019
	keyReadWrite = 0x2001F
	keyAll       = 0xF003F
	regSZ        = 1
	regDWORD     = 4
	errNoMore    = 259
)

type regKey uintptr

func regCreate(path string) (regKey, error) {
	var h uintptr
	r, _, _ := procRegCreateKeyExW.Call(hkcu, uintptr(unsafe.Pointer(u16(path))), 0, 0, 0, keyAll, 0,
		uintptr(unsafe.Pointer(&h)), 0)
	if r != 0 {
		return 0, syscall.Errno(r)
	}
	return regKey(h), nil
}

func regOpen(path string, access uintptr) (regKey, bool) {
	var h uintptr
	r, _, _ := procRegOpenKeyExW.Call(hkcu, uintptr(unsafe.Pointer(u16(path))), 0, access, uintptr(unsafe.Pointer(&h)))
	return regKey(h), r == 0
}

func (k regKey) close() { procRegCloseKey.Call(uintptr(k)) }

func (k regKey) setString(name, val string) error {
	u := utf16.Encode([]rune(val + "\x00"))
	r, _, _ := procRegSetValueExW.Call(uintptr(k), uintptr(unsafe.Pointer(u16(name))), 0, regSZ,
		uintptr(unsafe.Pointer(&u[0])), uintptr(len(u)*2))
	if r != 0 {
		return syscall.Errno(r)
	}
	return nil
}

func (k regKey) setDword(name string, v uint32) error {
	r, _, _ := procRegSetValueExW.Call(uintptr(k), uintptr(unsafe.Pointer(u16(name))), 0, regDWORD,
		uintptr(unsafe.Pointer(&v)), 4)
	if r != 0 {
		return syscall.Errno(r)
	}
	return nil
}

func (k regKey) getString(name string) string {
	buf := make([]uint16, 1024)
	size := uint32(len(buf) * 2)
	var typ uint32
	r, _, _ := procRegQueryValueExW.Call(uintptr(k), uintptr(unsafe.Pointer(u16(name))), 0,
		uintptr(unsafe.Pointer(&typ)), uintptr(unsafe.Pointer(&buf[0])), uintptr(unsafe.Pointer(&size)))
	if r != 0 || typ != regSZ {
		return ""
	}
	return syscall.UTF16ToString(buf[:size/2])
}

func (k regKey) valueNames() []string {
	var out []string
	buf := make([]uint16, 16384)
	for i := uint32(0); ; i++ {
		n := uint32(len(buf))
		r, _, _ := procRegEnumValueW.Call(uintptr(k), uintptr(i), uintptr(unsafe.Pointer(&buf[0])),
			uintptr(unsafe.Pointer(&n)), 0, 0, 0, 0)
		if r != 0 {
			break
		}
		out = append(out, syscall.UTF16ToString(buf[:n]))
	}
	return out
}

func (k regKey) subKeys() []string {
	var out []string
	buf := make([]uint16, 512)
	for i := uint32(0); ; i++ {
		n := uint32(len(buf))
		r, _, _ := procRegEnumKeyExW.Call(uintptr(k), uintptr(i), uintptr(unsafe.Pointer(&buf[0])),
			uintptr(unsafe.Pointer(&n)), 0, 0, 0, 0)
		if r != 0 {
			break
		}
		out = append(out, syscall.UTF16ToString(buf[:n]))
	}
	return out
}

func (k regKey) deleteValue(name string) {
	procRegDeleteValueW.Call(uintptr(k), uintptr(unsafe.Pointer(u16(name))))
}

func regDeleteTree(path string) {
	procRegDeleteTreeW.Call(hkcu, uintptr(unsafe.Pointer(u16(path))))
	procRegDeleteKeyW.Call(hkcu, uintptr(unsafe.Pointer(u16(path))))
}

func regGet(path, name string) string {
	k, ok := regOpen(path, keyRead)
	if !ok {
		return ""
	}
	defer k.close()
	return k.getString(name)
}

// ---------------------------------------------------------------------------
// Processes
// ---------------------------------------------------------------------------

type procInfo struct {
	pid              uint32
	name, image, cmd string
}

func processImage(h syscall.Handle) string {
	buf := make([]uint16, 1024)
	n := uint32(len(buf))
	r, _, _ := procQueryFullProcessImageNameW.Call(uintptr(h), 0, uintptr(unsafe.Pointer(&buf[0])), uintptr(unsafe.Pointer(&n)))
	if r == 0 {
		return ""
	}
	return syscall.UTF16ToString(buf[:n])
}

// processCmdLine uses ProcessCommandLineInformation (60), available since Windows 8.1.
func processCmdLine(h syscall.Handle) string {
	size := uint32(8192)
	for try := 0; try < 3; try++ {
		buf := make([]byte, size)
		var ret uint32
		st, _, _ := procNtQueryInformationProcess.Call(uintptr(h), 60, uintptr(unsafe.Pointer(&buf[0])),
			uintptr(size), uintptr(unsafe.Pointer(&ret)))
		if st == 0 {
			n := uintptr(*(*uint16)(unsafe.Pointer(&buf[0])))
			ptr := *(*uintptr)(unsafe.Pointer(&buf[unsafe.Sizeof(uintptr(0))]))
			base := uintptr(unsafe.Pointer(&buf[0]))
			if ptr < base || n == 0 {
				return ""
			}
			off := ptr - base
			if off+n > uintptr(len(buf)) {
				return ""
			}
			u := make([]uint16, n/2)
			for i := range u {
				u[i] = uint16(buf[off+uintptr(2*i)]) | uint16(buf[off+uintptr(2*i)+1])<<8
			}
			return string(utf16.Decode(u))
		}
		if uint32(st) == 0xC0000004 && ret > size { // STATUS_INFO_LENGTH_MISMATCH
			size = ret
			continue
		}
		return ""
	}
	return ""
}

// managerProcesses: Python running the manager, and the browser processes of the app window's
// private profile. Never the user's normal browser windows.
func managerProcesses(dest string) []procInfo {
	snap, err := syscall.CreateToolhelp32Snapshot(syscall.TH32CS_SNAPPROCESS, 0)
	if err != nil {
		return nil
	}
	defer syscall.CloseHandle(snap)
	self := uint32(os.Getpid())
	var out []procInfo
	var e syscall.ProcessEntry32
	e.Size = uint32(unsafe.Sizeof(e))
	for err = syscall.Process32First(snap, &e); err == nil; err = syscall.Process32Next(snap, &e) {
		name := strings.ToLower(syscall.UTF16ToString(e.ExeFile[:]))
		if e.ProcessID == self {
			continue
		}
		switch name {
		case "python.exe", "pythonw.exe", "msedge.exe", "chrome.exe", "brave.exe", "msedgewebview2.exe", "wow forever addons.exe":
		default:
			continue
		}
		h, err := syscall.OpenProcess(0x1000|0x0001, false, e.ProcessID) // QUERY_LIMITED_INFORMATION | TERMINATE
		if err != nil {
			continue
		}
		p := procInfo{pid: e.ProcessID, name: name, image: processImage(h), cmd: processCmdLine(h)}
		syscall.CloseHandle(h)
		if underDir(p.image, dest) || containsFold(p.cmd, "WoWForeverAddons.pyw") ||
			containsFold(p.cmd, `WoWForeverAddonManager\window`) || containsFold(p.cmd, `WoWForeverAddonManager\webview`) {
			out = append(out, p)
		}
	}
	return out
}

func killProcess(pid uint32) {
	h, err := syscall.OpenProcess(0x0001|0x00100000, false, pid) // TERMINATE | SYNCHRONIZE
	if err != nil {
		return
	}
	syscall.TerminateProcess(h, 1)
	syscall.WaitForSingleObject(h, 3000)
	syscall.CloseHandle(h)
}

// ---------------------------------------------------------------------------
// Shortcuts (IShellLinkW through COM)
// ---------------------------------------------------------------------------

type guid struct {
	d1     uint32
	d2, d3 uint16
	d4     [8]byte
}

var (
	clsidShellLink  = guid{0x00021401, 0, 0, [8]byte{0xC0, 0, 0, 0, 0, 0, 0, 0x46}}
	iidShellLinkW   = guid{0x000214F9, 0, 0, [8]byte{0xC0, 0, 0, 0, 0, 0, 0, 0x46}}
	iidPersistFile  = guid{0x0000010b, 0, 0, [8]byte{0xC0, 0, 0, 0, 0, 0, 0, 0x46}}
	iidPropStore    = guid{0x886D8EEB, 0x8CF2, 0x4446, [8]byte{0x8D, 0x02, 0xCD, 0xBA, 0x1D, 0xBD, 0xCF, 0x99}}
	fmtAppUserModel = guid{0x9F4C2855, 0x9F79, 0x4B39, [8]byte{0xA8, 0xD0, 0xE1, 0xD4, 0x2D, 0xE1, 0xD5, 0xF3}}
)

// Same ID the manager gives its window, so the taskbar groups the window with this shortcut
// (our icon and name, and pinning pins the manager - not Edge/Chrome).
const appUserModelID = "WoWForeverAddons.Manager"

type propertyKey struct {
	fmtid guid
	pid   uint32
}

type propVariant struct {
	vt         uint16
	r1, r2, r3 uint16
	p, p2      uintptr
}

func comCall(obj uintptr, idx int, args ...uintptr) uintptr {
	vtbl := *(*uintptr)(unsafe.Pointer(obj))
	fn := *(*uintptr)(unsafe.Pointer(vtbl + uintptr(idx)*unsafe.Sizeof(vtbl)))
	r, _, _ := syscall.SyscallN(fn, append([]uintptr{obj}, args...)...)
	return r
}

// withCOM runs f on one OS thread with COM initialised (single-threaded apartment).
func withCOM(f func() error) error {
	ch := make(chan error, 1)
	go func() {
		runtime.LockOSThread()
		defer runtime.UnlockOSThread()
		hr, _, _ := procCoInitializeEx.Call(0, 2)
		if int32(hr) >= 0 {
			defer procCoUninitialize.Call()
		}
		ch <- f()
	}()
	return <-ch
}

func makeShortcut(lnk, target, args, workdir, icon, desc string) error {
	var sl uintptr
	hr, _, _ := procCoCreateInstance.Call(uintptr(unsafe.Pointer(&clsidShellLink)), 0, 1,
		uintptr(unsafe.Pointer(&iidShellLinkW)), uintptr(unsafe.Pointer(&sl)))
	if int32(hr) < 0 || sl == 0 {
		return fmt.Errorf("Windows could not create a shortcut (0x%08x)", uint32(hr))
	}
	defer comCall(sl, 2) // Release
	pTarget, pArgs, pDir, pIcon, pDesc, pLnk := u16(target), u16(args), u16(workdir), u16(icon), u16(desc), u16(lnk)
	comCall(sl, 20, uintptr(unsafe.Pointer(pTarget)))
	comCall(sl, 11, uintptr(unsafe.Pointer(pArgs)))
	comCall(sl, 9, uintptr(unsafe.Pointer(pDir)))
	comCall(sl, 17, uintptr(unsafe.Pointer(pIcon)), 0)
	comCall(sl, 7, uintptr(unsafe.Pointer(pDesc)))
	var ps uintptr
	pID := u16(appUserModelID)
	if hr := comCall(sl, 0, uintptr(unsafe.Pointer(&iidPropStore)), uintptr(unsafe.Pointer(&ps))); int32(hr) >= 0 && ps != 0 {
		key := propertyKey{fmtAppUserModel, 5}
		pv := propVariant{vt: 31 /*VT_LPWSTR*/, p: uintptr(unsafe.Pointer(pID))}
		comCall(ps, 6, uintptr(unsafe.Pointer(&key)), uintptr(unsafe.Pointer(&pv))) // SetValue
		comCall(ps, 7)                                                              // Commit
		comCall(ps, 2)
		runtime.KeepAlive(&key)
		runtime.KeepAlive(&pv)
	}
	runtime.KeepAlive(pID)
	var pf uintptr
	hr = comCall(sl, 0, uintptr(unsafe.Pointer(&iidPersistFile)), uintptr(unsafe.Pointer(&pf)))
	if int32(hr) < 0 || pf == 0 {
		return fmt.Errorf("Windows could not save a shortcut (0x%08x)", uint32(hr))
	}
	defer comCall(pf, 2)
	hr = comCall(pf, 6, uintptr(unsafe.Pointer(pLnk)), 1)
	runtime.KeepAlive(pTarget)
	runtime.KeepAlive(pArgs)
	runtime.KeepAlive(pDir)
	runtime.KeepAlive(pIcon)
	runtime.KeepAlive(pDesc)
	runtime.KeepAlive(pLnk)
	if int32(hr) < 0 {
		return fmt.Errorf("Windows could not save the shortcut %s (0x%08x)", lnk, uint32(hr))
	}
	return nil
}

func refreshShell() { procSHChangeNotify.Call(0x08000000, 0, 0, 0) } // SHCNE_ASSOCCHANGED

// ---------------------------------------------------------------------------
// Authenticode: is the file validly signed, and by whom?
// ---------------------------------------------------------------------------

type wintrustFileInfo struct {
	cbStruct     uint32
	path         *uint16
	file         uintptr
	knownSubject uintptr
}

type wintrustData struct {
	cbStruct           uint32
	policyCallbackData uintptr
	sipClientData      uintptr
	uiChoice           uint32
	revocationChecks   uint32
	unionChoice        uint32
	fileInfo           *wintrustFileInfo
	stateAction        uint32
	stateData          uintptr
	urlReference       *uint16
	provFlags          uint32
	uiContext          uint32
	signatureSettings  uintptr
}

var actionGenericVerifyV2 = guid{0x00AAC56B, 0xCD44, 0x11d0, [8]byte{0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE}}

// signerOf returns the signer's name if the file carries a valid Authenticode signature
// that chains to a trusted root.
func signerOf(path string) (string, error) {
	p := u16(path)
	fi := wintrustFileInfo{path: p}
	fi.cbStruct = uint32(unsafe.Sizeof(fi))
	wd := wintrustData{uiChoice: 2 /*NONE*/, unionChoice: 1 /*FILE*/, fileInfo: &fi, stateAction: 1 /*VERIFY*/, provFlags: 0x10 /*no revocation*/}
	wd.cbStruct = uint32(unsafe.Sizeof(wd))
	noWindow := ^uintptr(0) // INVALID_HANDLE_VALUE: never show UI
	r, _, _ := procWinVerifyTrust.Call(noWindow, uintptr(unsafe.Pointer(&actionGenericVerifyV2)), uintptr(unsafe.Pointer(&wd)))
	defer func() {
		wd.stateAction = 2 // CLOSE
		procWinVerifyTrust.Call(noWindow, uintptr(unsafe.Pointer(&actionGenericVerifyV2)), uintptr(unsafe.Pointer(&wd)))
		runtime.KeepAlive(p)
		runtime.KeepAlive(&fi)
	}()
	if r != 0 {
		return "", fmt.Errorf("%s is not validly signed (0x%08x)", filepath.Base(path), uint32(r))
	}
	prov, _, _ := procWTHelperProvDataFromStateData.Call(wd.stateData)
	if prov == 0 {
		return "", errors.New("could not read the signature")
	}
	sgnr, _, _ := procWTHelperGetProvSignerFromChain.Call(prov, 0, 0, 0)
	if sgnr == 0 {
		return "", errors.New("could not read the signer")
	}
	pc, _, _ := procWTHelperGetProvCertFromChain.Call(sgnr, 0)
	if pc == 0 {
		return "", errors.New("could not read the certificate")
	}
	cert := *(*uintptr)(unsafe.Pointer(pc + unsafe.Sizeof(uintptr(0)))) // CRYPT_PROVIDER_CERT.pCert
	buf := make([]uint16, 512)
	n, _, _ := procCertGetNameStringW.Call(cert, 4 /*SIMPLE_DISPLAY*/, 0, 0, uintptr(unsafe.Pointer(&buf[0])), uintptr(len(buf)))
	if n <= 1 {
		return "", errors.New("the certificate has no name")
	}
	return syscall.UTF16ToString(buf[:n]), nil
}

func signedByPSF(path string) error {
	name, err := signerOf(path)
	if err != nil {
		return err
	}
	if !containsFold(name, "Python Software Foundation") {
		return fmt.Errorf("%s is signed by %q, not the Python Software Foundation", filepath.Base(path), name)
	}
	return nil
}

// ---------------------------------------------------------------------------
// Files
// ---------------------------------------------------------------------------

func clearReadOnly(root string) {
	filepath.Walk(root, func(p string, info os.FileInfo, err error) error {
		if err == nil && info.Mode().Perm()&0200 == 0 {
			os.Chmod(p, 0666)
		}
		return nil
	})
}

// removeHard deletes a file or folder, retrying while something still holds it.
func removeHard(path string, tries int) bool {
	for i := 0; i < tries; i++ {
		if _, err := os.Lstat(path); os.IsNotExist(err) {
			return true
		}
		if os.RemoveAll(path) == nil {
			return true
		}
		clearReadOnly(path)
		time.Sleep(600 * time.Millisecond)
	}
	_, err := os.Lstat(path)
	return os.IsNotExist(err)
}

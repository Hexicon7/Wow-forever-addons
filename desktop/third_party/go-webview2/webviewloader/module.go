package webviewloader

// Patched for WoW Forever Addons: WebView2Loader.dll is only ever loaded from the program's own
// folder by full path (it is installed next to the exe). No in-memory loading.

import (
	"fmt"
	"os"
	"path/filepath"
	"unsafe"

	"golang.org/x/sys/windows"
)

func loaderPath() string {
	exe, err := os.Executable()
	if err != nil {
		return "WebView2Loader.dll"
	}
	return filepath.Join(filepath.Dir(exe), "WebView2Loader.dll")
}

var (
	nativeModule                                       = windows.NewLazyDLL(loaderPath())
	nativeCreate                                       = nativeModule.NewProc("CreateCoreWebView2EnvironmentWithOptions")
	nativeCompareBrowserVersions                       = nativeModule.NewProc("CompareBrowserVersions")
	nativeGetAvailableCoreWebView2BrowserVersionString = nativeModule.NewProc("GetAvailableCoreWebView2BrowserVersionString")
)

func load(p *windows.LazyProc) error {
	if err := nativeModule.Load(); err != nil {
		return fmt.Errorf("unable to load WebView2Loader.dll: %w", err)
	}
	return p.Find()
}

// CompareBrowserVersions compares two WebView2 versions (-1, 0, 1).
func CompareBrowserVersions(v1 string, v2 string) (int, error) {
	_v1, err := windows.UTF16PtrFromString(v1)
	if err != nil {
		return 0, err
	}
	_v2, err := windows.UTF16PtrFromString(v2)
	if err != nil {
		return 0, err
	}
	if err := load(nativeCompareBrowserVersions); err != nil {
		return 0, err
	}
	var result int32
	hr, _, _ := nativeCompareBrowserVersions.Call(uintptr(unsafe.Pointer(_v1)), uintptr(unsafe.Pointer(_v2)), uintptr(unsafe.Pointer(&result)))
	if hr != 0 {
		return 0, fmt.Errorf("CompareBrowserVersions returned HRESULT 0x%X", hr)
	}
	return int(result), nil
}

// GetInstalledVersion returns the installed WebView2 runtime version, or "" if none is installed.
func GetInstalledVersion() (string, error) {
	if err := load(nativeGetAvailableCoreWebView2BrowserVersionString); err != nil {
		return "", err
	}
	var result *uint16
	hr, _, _ := nativeGetAvailableCoreWebView2BrowserVersionString.Call(0, uintptr(unsafe.Pointer(&result)))
	defer windows.CoTaskMemFree(unsafe.Pointer(result))
	if hr != uintptr(windows.S_OK) {
		if hr&0xFFFF == uintptr(windows.ERROR_FILE_NOT_FOUND) {
			return "", nil
		}
		return "", fmt.Errorf("GetAvailableCoreWebView2BrowserVersionString returned HRESULT 0x%X", hr)
	}
	return windows.UTF16PtrToString(result), nil
}

// CreateCoreWebView2EnvironmentWithOptions calls the loader's routine of the same name.
func CreateCoreWebView2EnvironmentWithOptions(browserExecutableFolder, userDataFolder *uint16, environmentOptions uintptr, environmentCompletedHandle uintptr) (uintptr, error) {
	if err := load(nativeCreate); err != nil {
		return 0, err
	}
	res, _, _ := nativeCreate.Call(
		uintptr(unsafe.Pointer(browserExecutableFolder)),
		uintptr(unsafe.Pointer(userDataFolder)),
		environmentOptions,
		environmentCompletedHandle,
	)
	return res, nil
}

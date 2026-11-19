package main

import (
	"fmt"
	"io"
	"syscall"
	"unsafe"
)

// Downloads go through Windows' own HTTP stack (WinHTTP): it uses the system proxy settings
// and certificate store, and keeps the installer small.

var (
	procWinHttpOpen, procWinHttpSetTimeouts, procWinHttpConnect, procWinHttpOpenRequest,
	procWinHttpSendRequest, procWinHttpReceiveResponse, procWinHttpQueryHeaders,
	procWinHttpReadData, procWinHttpCloseHandle *syscall.LazyProc
)

func initHTTP() {
	w := sysDLL("winhttp.dll")
	procWinHttpOpen = w.NewProc("WinHttpOpen")
	procWinHttpSetTimeouts = w.NewProc("WinHttpSetTimeouts")
	procWinHttpConnect = w.NewProc("WinHttpConnect")
	procWinHttpOpenRequest = w.NewProc("WinHttpOpenRequest")
	procWinHttpSendRequest = w.NewProc("WinHttpSendRequest")
	procWinHttpReceiveResponse = w.NewProc("WinHttpReceiveResponse")
	procWinHttpQueryHeaders = w.NewProc("WinHttpQueryHeaders")
	procWinHttpReadData = w.NewProc("WinHttpReadData")
	procWinHttpCloseHandle = w.NewProc("WinHttpCloseHandle")
}

// httpDo sends one request and streams the body to out (if not nil). prog may be nil.
func httpDo(method, url, headers string, body []byte, timeoutMs int, out io.Writer, prog func(done, total int64)) (int, error) {
	secure, host, port, path, err := splitURL(url)
	if err != nil {
		return 0, err
	}
	agent := u16("WoWForeverAddons-Setup/" + appVersion)
	ses, _, e := procWinHttpOpen.Call(uintptr(unsafe.Pointer(agent)), 4 /*AUTOMATIC_PROXY*/, 0, 0, 0)
	if ses == 0 {
		ses, _, e = procWinHttpOpen.Call(uintptr(unsafe.Pointer(agent)), 0 /*DEFAULT_PROXY*/, 0, 0, 0)
	}
	if ses == 0 {
		return 0, fmt.Errorf("WinHTTP: %v", e)
	}
	defer procWinHttpCloseHandle.Call(ses)
	t := uintptr(timeoutMs)
	procWinHttpSetTimeouts.Call(ses, t, t, t, t)
	conn, _, e := procWinHttpConnect.Call(ses, uintptr(unsafe.Pointer(u16(host))), uintptr(port), 0)
	if conn == 0 {
		return 0, fmt.Errorf("could not connect to %s: %v", host, e)
	}
	defer procWinHttpCloseHandle.Call(conn)
	flags := uintptr(0)
	if secure {
		flags = 0x00800000 // WINHTTP_FLAG_SECURE
	}
	req, _, e := procWinHttpOpenRequest.Call(conn, uintptr(unsafe.Pointer(u16(method))), uintptr(unsafe.Pointer(u16(path))),
		0, 0, 0, flags)
	if req == 0 {
		return 0, fmt.Errorf("could not open the request: %v", e)
	}
	defer procWinHttpCloseHandle.Call(req)
	var hdr, bodyPtr uintptr
	hdrLen := uintptr(0)
	if headers != "" {
		hdr, hdrLen = uintptr(unsafe.Pointer(u16(headers))), ^uintptr(0)&0xFFFFFFFF // -1L: NUL-terminated
	}
	if len(body) > 0 {
		bodyPtr = uintptr(unsafe.Pointer(&body[0]))
	}
	r, _, e := procWinHttpSendRequest.Call(req, hdr, hdrLen, bodyPtr, uintptr(len(body)), uintptr(len(body)), 0)
	if r == 0 {
		return 0, fmt.Errorf("could not reach %s: %v", host, e)
	}
	if r, _, e = procWinHttpReceiveResponse.Call(req, 0); r == 0 {
		return 0, fmt.Errorf("no answer from %s: %v", host, e)
	}
	query := func(info uintptr) int64 {
		var v, size uint32 = 0, 4
		if r, _, _ := procWinHttpQueryHeaders.Call(req, info|0x20000000 /*FLAG_NUMBER*/, 0,
			uintptr(unsafe.Pointer(&v)), uintptr(unsafe.Pointer(&size)), 0); r == 0 {
			return -1
		}
		return int64(v)
	}
	status := int(query(19 /*STATUS_CODE*/))
	total := query(5 /*CONTENT_LENGTH*/)
	if out == nil || status != 200 {
		return status, nil
	}
	buf := make([]byte, 128*1024)
	var done int64
	for {
		var n uint32
		if r, _, e := procWinHttpReadData.Call(req, uintptr(unsafe.Pointer(&buf[0])), uintptr(len(buf)), uintptr(unsafe.Pointer(&n))); r == 0 {
			return status, fmt.Errorf("the download was interrupted: %v", e)
		}
		if n == 0 {
			break
		}
		if _, err := out.Write(buf[:n]); err != nil {
			return status, err
		}
		done += int64(n)
		if prog != nil {
			prog(done, total)
		}
	}
	if total > 0 && done != total {
		return status, fmt.Errorf("the download was cut short (%d of %d bytes)", done, total)
	}
	return status, nil
}

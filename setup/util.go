package main

import (
	"fmt"
	"strings"
	"unicode/utf16"
)

// Pure helpers (no Windows calls) so they can be unit-tested anywhere.

func containsFold(s, sub string) bool {
	return sub != "" && strings.Contains(strings.ToLower(s), strings.ToLower(sub))
}

// isOurs reports whether text (a path, command line, registry value name...) refers to the manager.
func isOurs(text string, marks []string) bool {
	for _, m := range marks {
		if containsFold(text, m) {
			return true
		}
	}
	return false
}

// rot13 decodes/encodes the names Windows stores under Explorer\UserAssist.
func rot13(s string) string {
	b := []rune(s)
	for i, c := range b {
		switch {
		case c >= 'A' && c <= 'Z':
			b[i] = 'A' + (c-'A'+13)%26
		case c >= 'a' && c <= 'z':
			b[i] = 'a' + (c-'a'+13)%26
		}
	}
	return string(b)
}

// lnkMentions looks for any mark inside a .lnk file's raw bytes, as ANSI or UTF-16LE text
// at either byte alignment (strings inside .lnk files are not always 2-byte aligned).
func lnkMentions(data []byte, marks []string) bool {
	if isOurs(string(data), marks) {
		return true
	}
	for start := 0; start < 2; start++ {
		n := (len(data) - start) / 2
		u := make([]uint16, n)
		for i := 0; i < n; i++ {
			u[i] = uint16(data[start+2*i]) | uint16(data[start+2*i+1])<<8
		}
		if isOurs(string(utf16.Decode(u)), marks) {
			return true
		}
	}
	return false
}

type options struct {
	uninstall, quiet, relocated, restart, tray bool
}

func parseArgs(args []string) options {
	var o options
	for _, a := range args {
		switch strings.ToLower(strings.TrimLeft(a, "/-")) {
		case "uninstall", "u":
			o.uninstall = true
		case "quiet", "q", "s", "silent", "verysilent":
			o.quiet = true
		case "relocated":
			o.relocated = true
		case "restart":
			o.restart = true
		case "tray":
			o.tray = true
		}
	}
	return o
}

// pythonArch picks the embeddable Python build: IMAGE_FILE_MACHINE_ARM64 (0xAA64) -> arm64.
func pythonArch(nativeMachine uint16, envArch string) string {
	if nativeMachine == 0xAA64 {
		return "arm64"
	}
	if nativeMachine == 0 && strings.EqualFold(envArch, "ARM64") {
		return "arm64"
	}
	return "amd64"
}

// underDir reports whether path is dir itself or inside it (case-insensitive, Windows-style).
func underDir(path, dir string) bool {
	p := strings.ToLower(strings.TrimRight(strings.ReplaceAll(path, "/", `\`), `\`))
	d := strings.ToLower(strings.TrimRight(strings.ReplaceAll(dir, "/", `\`), `\`))
	return d != "" && (p == d || strings.HasPrefix(p, d+`\`))
}

// safeZipName rejects absolute paths and ".." so a zip can't write outside its folder.
func safeZipName(name string) bool {
	n := strings.ReplaceAll(name, `\`, "/")
	if n == "" || strings.HasPrefix(n, "/") || strings.Contains(n, ":") {
		return false
	}
	for _, part := range strings.Split(n, "/") {
		if part == ".." {
			return false
		}
	}
	return true
}

func splitURL(url string) (secure bool, host string, port uint16, path string, err error) {
	rest := ""
	switch {
	case strings.HasPrefix(url, "https://"):
		secure, port, rest = true, 443, url[8:]
	case strings.HasPrefix(url, "http://"):
		port, rest = 80, url[7:]
	default:
		return false, "", 0, "", fmt.Errorf("bad URL %s", url)
	}
	host, path = rest, "/"
	if i := strings.IndexByte(rest, '/'); i >= 0 {
		host, path = rest[:i], rest[i:]
	}
	if i := strings.LastIndexByte(host, ':'); i >= 0 {
		var p int
		if _, err := fmt.Sscanf(host[i+1:], "%d", &p); err != nil || p <= 0 || p > 65535 {
			return false, "", 0, "", fmt.Errorf("bad URL %s", url)
		}
		host, port = host[:i], uint16(p)
	}
	return
}

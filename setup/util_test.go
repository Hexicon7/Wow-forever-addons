package main

import (
	"testing"
	"unicode/utf16"
)

func TestRot13(t *testing.T) {
	if rot13("P:\\Hfref") != "C:\\Users" || rot13(rot13("WoW Forever Addons.lnk")) != "WoW Forever Addons.lnk" {
		t.Fatal(rot13("P:\\Hfref"))
	}
}

func TestIsOurs(t *testing.T) {
	m := []string{`C:\Users\a\AppData\Local\Programs\WoW Forever Addons`, "WoWForeverAddonManager"}
	if !isOurs(`c:\users\A\appdata\local\programs\wow forever addons\python\pythonw.exe.FriendlyAppName`, m) {
		t.Fatal("path not matched")
	}
	if isOurs(`C:\Program Files\Mozilla Firefox\firefox.exe`, m) || isOurs("", m) {
		t.Fatal("false positive")
	}
	if isOurs("anything", []string{""}) {
		t.Fatal("empty mark matched")
	}
}

func TestLnkMentions(t *testing.T) {
	m := []string{"WoWForeverAddons.pyw"}
	enc := func(s string, off int) []byte {
		b := make([]byte, off)
		for _, u := range utf16.Encode([]rune(s)) {
			b = append(b, byte(u), byte(u>>8))
		}
		return append(b, 0, 0, 7)
	}
	for off := 0; off < 2; off++ {
		if !lnkMentions(enc(`"C:\x\WoWForeverAddons.pyw"`, 3+off), m) {
			t.Fatalf("utf16 at offset %d missed", off)
		}
	}
	if !lnkMentions([]byte("xx C:\\x\\WOWFOREVERADDONS.PYW yy"), m) {
		t.Fatal("ansi missed")
	}
	if lnkMentions(enc(`C:\Games\Other.exe`, 1), m) {
		t.Fatal("false positive")
	}
}

func TestArgs(t *testing.T) {
	o := parseArgs([]string{"/uninstall", "-quiet", "--relocated"})
	if !o.uninstall || !o.quiet || !o.relocated {
		t.Fatal(o)
	}
	if o := parseArgs([]string{"/S"}); o.uninstall || !o.quiet {
		t.Fatal(o)
	}
	if o := parseArgs([]string{"/quiet", "/restart", "/tray"}); !o.tray || !o.restart {
		t.Fatalf("tray: %+v", o)
	}
	if o := parseArgs([]string{"/quiet", "/restart"}); !o.quiet || !o.restart || o.uninstall {
		t.Fatal(o)
	}
	if o := parseArgs(nil); o.uninstall || o.quiet {
		t.Fatal(o)
	}
}

func TestArchAndPaths(t *testing.T) {
	if pythonArch(0xAA64, "AMD64") != "arm64" || pythonArch(0x8664, "ARM64") != "amd64" || pythonArch(0, "ARM64") != "arm64" || pythonArch(0, "x86") != "amd64" {
		t.Fatal("arch")
	}
	d := `C:\Users\a\AppData\Local\Programs\WoW Forever Addons`
	if !underDir(d+`\Uninstall.exe`, d) || !underDir(d, d+`\`) || underDir(d+` Old\x.exe`, d) || underDir(`C:\x`, "") {
		t.Fatal("underDir")
	}
	for _, bad := range []string{"../x", `..\x`, "/abs", `C:\x`, "a/../../b", ""} {
		if safeZipName(bad) {
			t.Fatal("unsafe accepted:", bad)
		}
	}
	if !safeZipName("python313.zip") || !safeZipName("Lib/x.py") {
		t.Fatal("safe rejected")
	}
}

func TestSplitURL(t *testing.T) {
	s, h, p, path, err := splitURL("https://www.python.org/ftp/python/3.13.16/python-3.13.16-embed-amd64.zip")
	if err != nil || !s || h != "www.python.org" || p != 443 || path != "/ftp/python/3.13.16/python-3.13.16-embed-amd64.zip" {
		t.Fatal(s, h, p, path, err)
	}
	s, h, p, path, err = splitURL("http://127.0.0.1:47811/api/quit")
	if err != nil || s || h != "127.0.0.1" || p != 47811 || path != "/api/quit" {
		t.Fatal(s, h, p, path, err)
	}
	if _, _, _, _, err := splitURL("ftp://x"); err == nil {
		t.Fatal("ftp accepted")
	}
	if _, _, _, _, err := splitURL("http://h:99999/"); err == nil {
		t.Fatal("bad port accepted")
	}
}

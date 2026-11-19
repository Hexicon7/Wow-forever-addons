#!/bin/sh
# Builds dist/WoWForeverAddons-Setup-<version>.exe (needs: Go 1.24+, llvm-windres, Python 3).
set -e
cd "$(dirname "$0")"
WR=${WR:-llvm-windres}
command -v "$WR" >/dev/null 2>&1 || WR=/usr/lib/llvm-18/bin/llvm-windres
V=$(grep -oP 'APP_VERSION = "\K[^"]+' app/WoWForeverAddons.pyw)
C=$(echo "$V" | tr . ,)
python3 tools/splice.py --repo .
python3 app/WoWForeverAddons.pyw --write-icon setup/payload/icon.ico
cp setup/payload/icon.ico desktop/payload/icon.ico
python3 tools/verify_authenticode.py desktop/payload/WebView2Loader.dll >/dev/null
sed -E -i "s/(name=\"WoWForeverAddons.App\" version=\")[0-9.]+(\")/\1$V.0\2/" desktop/app.manifest
sed -E -i "s/(name=\"WoWForeverAddons.Setup\" version=\")[0-9.]+(\")/\1$V.0\2/" setup/app.manifest
sed -E -i "s/^(FILEVERSION|PRODUCTVERSION) .*/\1 $C,0/; s/(\"(FileVersion|ProductVersion)\", \")[^\"]+/\1$V/" desktop/app.rc setup/setup.rc
sed -E -i "s/WoWForeverAddons-Setup-[0-9.]+\.exe/WoWForeverAddons-Setup-$V.exe/" setup/setup.rc
(cd desktop && $WR --target=pe-x86-64 -i app.rc -o rsrc_windows_amd64.syso &&
  GOFLAGS=-mod=mod GOPROXY=off GOSUMDB=off GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -H windowsgui" -o ../setup/payload/launcher.exe .)
cp app/WoWForeverAddons.pyw app/README.txt setup/payload/
cp desktop/payload/WebView2Loader.dll setup/payload/
(cd setup && $WR --target=pe-x86-64 -i setup.rc -o rsrc_windows_amd64.syso && go test ./... >/dev/null &&
  GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -H windowsgui -X main.appVersion=$V" -o "../dist/WoWForeverAddons-Setup-$V.exe" .)
sha256sum "dist/WoWForeverAddons-Setup-$V.exe"

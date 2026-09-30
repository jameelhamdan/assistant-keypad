# Keypad - build everything from the repo root.
#   make test        host + firmware unit tests
#   make firmware    build the keypad firmware (PlatformIO)
#   make flash       flash it over USB (stop the Keypad agent first)
#   make host        build the keypad binary for this machine
#   make mac         Keypad.app + DMG (macOS)
#   make windows     keypad.exe + keypadw.exe (cross-compiled); installer: packaging/windows/keypad.iss

VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)
FW_VERSION ?= $(patsubst v%,%,$(VERSION))
LDFLAGS := -s -w -X main.version=$(VERSION) -X github.com/jameelhamdan/assistant-keypad/host/internal/firmware.Version=$(FW_VERSION)
FW_BIN := firmware/.pio/build/keypad/firmware.bin
DIST := dist

.PHONY: test test-host test-firmware firmware flash bundle-firmware host mac windows clean

test: test-host test-firmware

test-host:
	cd host && go vet ./... && go test -race ./...

test-firmware:
	cd firmware && pio test -e native

firmware:
	cd firmware && PLATFORMIO_BUILD_FLAGS='-DKEYPAD_FW_VERSION=\"$(FW_VERSION)\"' pio run -e keypad

flash:
	cd firmware && pio run -e keypad -t upload

# Embed the firmware image in the host binary (enables "Update firmware" in Settings).
bundle-firmware: firmware
	cp $(FW_BIN) host/internal/firmware/bin/keypad.bin

host:
	mkdir -p $(DIST)
	cd host && go build -trimpath -ldflags "$(LDFLAGS)" -o ../$(DIST)/keypad ./cmd/keypad

mac: bundle-firmware
	VERSION=$(VERSION) LDFLAGS="$(LDFLAGS)" packaging/macos/build.sh

windows: bundle-firmware
	mkdir -p $(DIST)/windows
	cd host && GOOS=windows GOARCH=amd64 go build -trimpath -ldflags "$(LDFLAGS)" -o ../$(DIST)/windows/keypad.exe ./cmd/keypad
	cd host && GOOS=windows GOARCH=amd64 go build -trimpath -ldflags "$(LDFLAGS) -H windowsgui" -o ../$(DIST)/windows/keypadw.exe ./cmd/keypad

clean:
	rm -rf $(DIST) firmware/.pio/build host/internal/firmware/bin/keypad.bin

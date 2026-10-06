# Keypad - build everything from the repo root.
#   make setup       create the Python environment (host/.venv, needs uv)
#   make test        host + firmware unit tests
#   make firmware    build the keypad firmware (PlatformIO)
#   make sim         firmware simulator tests (needs ziglang, pillow)
#   make flash       flash it over USB (quit Keypad first if it holds the port)
#   make mac         Keypad.app + DMG (macOS)
#   Windows:         packaging\windows\build.ps1 -Version x.y.z

VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)
FW_VERSION ?= $(patsubst v%,%,$(VERSION))
FW_BIN := firmware/.pio/build/keypad/firmware.bin
DATA := host/keypad/data

.PHONY: setup test test-host test-firmware lint firmware flash sim bundle-firmware mac clean

setup:
	cd host && uv sync

test: test-host test-firmware

test-host: setup
	cd host && .venv/bin/python -m pytest -q

lint: setup
	cd host && .venv/bin/ruff check keypad tests

test-firmware:
	cd firmware && pio test -e native

firmware:
	cd firmware && PLATFORMIO_BUILD_FLAGS='-DKEYPAD_FW_VERSION=\"$(FW_VERSION)\"' pio run -e keypad

flash:
	cd firmware && PLATFORMIO_BUILD_FLAGS='-DKEYPAD_FW_VERSION=\"$(FW_VERSION)\"' pio run -e keypad -t upload

sim: setup firmware
	uv pip install -q --python host/.venv ziglang
	host/.venv/bin/python firmware/sim/build.py
	host/.venv/bin/python -m pytest firmware/sim -q

# Bundle the firmware image (enables "Update firmware" in the tray).
bundle-firmware: firmware
	cp $(FW_BIN) $(DATA)/keypad.bin
	printf '%s\n' "$(FW_VERSION)" > $(DATA)/firmware-version

mac: setup bundle-firmware
	VERSION=$(VERSION) packaging/macos/build.sh

clean:
	rm -rf dist firmware/.pio/build $(DATA)/keypad.bin $(DATA)/firmware-version

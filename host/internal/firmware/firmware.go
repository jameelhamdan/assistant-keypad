// Package firmware bundles the keypad firmware image so the tray can update
// keypads over USB or Wi-Fi. The release build copies the PlatformIO output
// to bin/keypad.bin; development builds simply have no bundled image.
package firmware

import "embed"

//go:embed bin
var files embed.FS

// Version is set at build time (-ldflags "-X .../firmware.Version=2.0.0").
var Version = "dev"

// Image returns the bundled firmware, or nil.
func Image() []byte {
	b, err := files.ReadFile("bin/keypad.bin")
	if err != nil || len(b) == 0 {
		return nil
	}
	return b
}

package main

import (
	"golang.org/x/sys/windows"
)

// trayLock returns false if another tray already runs for this user.
func trayLock() bool {
	name, _ := windows.UTF16PtrFromString(`Local\KeypadTray`)
	_, err := windows.CreateMutex(nil, false, name)
	return err != windows.ERROR_ALREADY_EXISTS
}

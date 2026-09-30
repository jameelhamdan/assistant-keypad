package osutil

import (
	"unsafe"

	"golang.org/x/sys/windows"
)

// ParentPID returns the parent of pid, or 0.
func ParentPID(pid int) int {
	snap, err := windows.CreateToolhelp32Snapshot(windows.TH32CS_SNAPPROCESS, 0)
	if err != nil {
		return 0
	}
	defer windows.CloseHandle(snap)
	var e windows.ProcessEntry32
	e.Size = uint32(unsafe.Sizeof(e))
	for err = windows.Process32First(snap, &e); err == nil; err = windows.Process32Next(snap, &e) {
		if int(e.ProcessID) == pid {
			return int(e.ParentProcessID)
		}
	}
	return 0
}

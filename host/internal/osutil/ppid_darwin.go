package osutil

import "golang.org/x/sys/unix"

// ParentPID returns the parent of pid, or 0.
func ParentPID(pid int) int {
	k, err := unix.SysctlKinfoProc("kern.proc.pid", pid)
	if err != nil {
		return 0
	}
	return int(k.Eproc.Ppid)
}

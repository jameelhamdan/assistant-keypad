//go:build !darwin && !windows

package osutil

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

// ParentPID returns the parent of pid, or 0.
func ParentPID(pid int) int {
	b, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", pid))
	if err != nil {
		return 0
	}
	f := strings.Fields(string(b[strings.LastIndexByte(string(b), ')')+1:]))
	if len(f) < 2 {
		return 0
	}
	n, _ := strconv.Atoi(f[1])
	return n
}

package ipc

import (
	"context"
	"errors"
	"net"
	"os"
	"strings"

	"github.com/Microsoft/go-winio"
	"golang.org/x/sys/windows"
)

func pipeName() string {
	u := strings.NewReplacer(`\`, "-", " ", "-").Replace(os.Getenv("USERNAME"))
	return `\\.\pipe\keypad-agent-` + u
}

func dial(ctx context.Context) (net.Conn, error) { return winio.DialPipeContext(ctx, pipeName()) }

func listen() (net.Listener, error) {
	if Alive() {
		return nil, errors.New("another agent is already running")
	}
	tok := windows.GetCurrentProcessToken()
	u, err := tok.GetTokenUser()
	if err != nil {
		return nil, err
	}
	// Only the current user (and SYSTEM) may open the pipe.
	sddl := "D:P(A;;GA;;;" + u.User.Sid.String() + ")(A;;GA;;;SY)"
	return winio.ListenPipe(pipeName(), &winio.PipeConfig{SecurityDescriptor: sddl})
}

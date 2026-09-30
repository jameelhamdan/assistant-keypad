// Package device owns every connection to keypads: USB serial and paired
// Wi-Fi links, handshakes, liveness, and fan-out of messages.
package device

import (
	"bufio"
	"bytes"
	"errors"
	"net"
	"strings"
	"sync"
	"time"

	"go.bug.st/serial"
	"go.bug.st/serial/enumerator"

	"github.com/jameelhamdan/assistant-keypad/host/internal/proto"
	"github.com/jameelhamdan/assistant-keypad/host/internal/secure"
)

// Link is one byte-stream transport carrying whole messages.
type Link interface {
	Kind() string // "usb" | "wifi" | "fake"
	Addr() string
	Send(msg []byte) error
	Recv() ([]byte, error)
	Close() error
}

// ---- USB --------------------------------------------------------------------

type usbLink struct {
	port serial.Port
	name string
	r    *bufio.Reader
	wmu  sync.Mutex
}

func openUSB(name string) (*usbLink, error) {
	p, err := serial.Open(name, &serial.Mode{BaudRate: 115200})
	if err != nil {
		return nil, err
	}
	_ = p.SetDTR(true) // native USB CDC only transmits once DTR is set
	return &usbLink{port: p, name: name, r: bufio.NewReaderSize(p, 4096)}, nil
}

func (l *usbLink) Kind() string { return "usb" }
func (l *usbLink) Addr() string { return l.name }
func (l *usbLink) Close() error { return l.port.Close() }

func (l *usbLink) Send(msg []byte) error {
	l.wmu.Lock()
	defer l.wmu.Unlock()
	_, err := l.port.Write(append(msg, '\n'))
	return err
}

func (l *usbLink) Recv() ([]byte, error) {
	for {
		line, err := l.r.ReadSlice('\n')
		if errors.Is(err, bufio.ErrBufferFull) {
			// oversized line: skip to the next newline
			for errors.Is(err, bufio.ErrBufferFull) {
				_, err = l.r.ReadSlice('\n')
			}
			continue
		}
		if err != nil {
			return nil, err
		}
		line = bytes.TrimSpace(line)
		if len(line) == 0 || line[0] != '{' {
			continue // boot ROM chatter, blank lines
		}
		return append([]byte(nil), line...), nil
	}
}

// usbPorts lists serial ports that belong to an Espressif native-USB device.
func usbPorts() []string {
	list, err := enumerator.GetDetailedPortsList()
	if err != nil {
		return nil
	}
	var out []string
	for _, p := range list {
		if p.IsUSB && strings.EqualFold(p.VID, proto.USBVendorID) {
			out = append(out, p.Name)
		}
	}
	return out
}

// ---- Wi-Fi --------------------------------------------------------------------

type wifiLink struct {
	c    net.Conn
	s    *secure.Conn
	addr string
}

func dialWiFi(addr, hostID, deviceID, key string) (*wifiLink, error) {
	c, err := net.DialTimeout("tcp", addr, 3*time.Second)
	if err != nil {
		return nil, err
	}
	_ = c.SetDeadline(time.Now().Add(5 * time.Second))
	s, err := secure.ClientHandshake(c, hostID, deviceID, key)
	if err != nil {
		c.Close()
		return nil, err
	}
	_ = c.SetDeadline(time.Time{})
	if tc, ok := c.(*net.TCPConn); ok {
		_ = tc.SetKeepAlive(true)
		_ = tc.SetNoDelay(true)
	}
	return &wifiLink{c: c, s: s, addr: addr}, nil
}

func (l *wifiLink) Kind() string          { return "wifi" }
func (l *wifiLink) Addr() string          { return l.addr }
func (l *wifiLink) Send(msg []byte) error { return l.s.Send(msg) }
func (l *wifiLink) Recv() ([]byte, error) { return l.s.Recv() }
func (l *wifiLink) Close() error          { return l.c.Close() }

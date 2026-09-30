// Package ipc is the private channel between the agent and its local
// clients (hook shim, MCP shim, tray): HTTP over a Unix socket (mode 0600)
// or a Windows named pipe that only the current user can open. Nothing
// listens on a TCP port, so other users and web pages cannot reach it.
package ipc

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"time"
)

// Client talks to the agent.
type Client struct{ hc *http.Client }

func NewClient() *Client {
	return &Client{hc: &http.Client{Transport: &http.Transport{
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) { return dial(ctx) },
	}}}
}

// Do sends body (JSON) to path and decodes the reply into out.
func (c *Client) Do(ctx context.Context, method, path string, body, out any) error {
	var r io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return err
		}
		r = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, "http://keypad"+path, r)
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.hc.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if resp.StatusCode >= 300 {
		var e struct{ Error string }
		_ = json.Unmarshal(b, &e)
		if e.Error == "" {
			e.Error = resp.Status
		}
		return fmt.Errorf("%s", e.Error)
	}
	if out != nil {
		return json.Unmarshal(b, out)
	}
	return nil
}

// Alive reports whether an agent is listening.
func Alive() bool {
	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()
	c, err := dial(ctx)
	if err != nil {
		return false
	}
	c.Close()
	return true
}

// Listen opens the agent's endpoint.
func Listen() (net.Listener, error) { return listen() }

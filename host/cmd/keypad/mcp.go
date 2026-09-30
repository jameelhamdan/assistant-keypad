package main

import (
	"context"
	"fmt"
	"os"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/jameelhamdan/assistant-keypad/host/internal/core"
	"github.com/jameelhamdan/assistant-keypad/host/internal/ipc"
)

const unavailable = "KEYPAD UNAVAILABLE"

type askIn struct {
	Question string   `json:"question" jsonschema:"the question, short enough for a small screen"`
	Options  []string `json:"options,omitempty" jsonschema:"up to 32 short choices (8 per page); omit for a yes/no question"`
	Multi    bool     `json:"multi,omitempty" jsonschema:"let the user pick several options (at most 7)"`
	Header   string   `json:"header,omitempty" jsonschema:"very short title, e.g. DATABASE"`
}

type notifyIn struct {
	Text string `json:"text" jsonschema:"a short message (60 characters)"`
}

func text(s string) *mcp.CallToolResult {
	return &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: s}}}
}

func runMCP() error {
	s := mcp.NewServer(&mcp.Implementation{Name: "keypad", Version: version}, &mcp.ServerOptions{
		Instructions: "ask_user asks the human on their hardware keypad (small color screen, 8 keys). " +
			"Use it for decisions with enumerable answers: yes/no, or a few short options. If the result starts with '" +
			unavailable + "', ask in the conversation instead and do not retry.",
	})
	client := ipc.NewClient()
	cwd, _ := os.Getwd()

	mcp.AddTool(s, &mcp.Tool{Name: "ask_user", Description: "Ask the user a question on the hardware keypad and wait for the answer."},
		func(ctx context.Context, _ *mcp.CallToolRequest, in askIn) (*mcp.CallToolResult, any, error) {
			q := core.Question{Text: in.Question, Header: in.Header, Options: in.Options, Multi: in.Multi, YesNo: len(in.Options) == 0}
			var ans []struct {
				Values []string `json:"values"`
				Yes    bool     `json:"yes"`
				Error  string   `json:"error"`
			}
			err := client.Do(ctx, "POST", "/ask", map[string]any{"pid": os.Getppid(), "cwd": cwd, "questions": []core.Question{q}}, &ans)
			switch {
			case err != nil:
				return text(unavailable + ": the keypad agent is not running."), nil, nil
			case len(ans) == 0 || ans[0].Error != "":
				why := "no answer"
				if len(ans) > 0 {
					why = ans[0].Error
				}
				return text(fmt.Sprintf("%s: %s. Ask the user in the conversation instead.", unavailable, why)), nil, nil
			case q.YesNo:
				return text(map[bool]string{true: "The user answered: yes", false: "The user answered: no"}[ans[0].Yes]), nil, nil
			default:
				return text("The user answered: " + strings.Join(ans[0].Values, ", ")), nil, nil
			}
		})

	mcp.AddTool(s, &mcp.Tool{Name: "notify", Description: "Show a short message on the hardware keypad."},
		func(ctx context.Context, _ *mcp.CallToolRequest, in notifyIn) (*mcp.CallToolResult, any, error) {
			if err := client.Do(ctx, "POST", "/toast", map[string]string{"text": in.Text, "level": "info"}, nil); err != nil {
				return text(unavailable), nil, nil
			}
			return text("Shown on the keypad."), nil, nil
		})

	return s.Run(context.Background(), &mcp.StdioTransport{})
}

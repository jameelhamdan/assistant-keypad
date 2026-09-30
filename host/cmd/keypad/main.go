// Command keypad is the whole PC side of the hardware keypad in one binary:
//
//	keypad agent        background agent: owns the keypads, answers hooks (started at login)
//	keypad tray         tray icon and menu
//	keypad hook <Event> Claude Code command hook (installed into ~/.claude/settings.json)
//	keypad mcp          Claude Code MCP server (stdio)
//	keypad install      register Claude Code integration + start at login
//	keypad uninstall    remove both
//	keypad claude install|uninstall|status
//	keypad service install|uninstall|status
//	keypad status       show keypads and sessions
//	keypad settings     open the settings page
//	keypad update <keypad-id> <firmware.bin>
//	keypad version
package main

import (
	"fmt"
	"os"
)

var version = "dev"

func main() {
	args := os.Args[1:]
	if len(args) == 0 {
		args = []string{defaultCommand()}
	}
	var err error
	switch args[0] {
	case "hook":
		os.Exit(runHook(args[1:]))
	case "mcp":
		err = runMCP()
	case "agent":
		err = runAgent(args[1:])
	case "tray":
		err = runTray()
	case "install":
		err = cmdInstall()
	case "uninstall":
		err = cmdUninstall()
	case "claude":
		err = cmdClaude(args[1:])
	case "service":
		err = cmdService(args[1:])
	case "status":
		err = cmdStatus()
	case "settings", "ui":
		err = cmdSettings()
	case "update":
		err = cmdUpdate(args[1:])
	case "version", "--version", "-v":
		fmt.Println("keypad", version)
	case "help", "--help", "-h":
		usage()
	default:
		usage()
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "keypad:", err)
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprint(os.Stderr, `usage: keypad <command>

  agent               run the background agent (normally started at login)
  tray                show the tray icon
  install             connect Claude Code and start Keypad at login
  uninstall           undo install
  claude  install|uninstall|status
  service install|uninstall|status
  status              show keypads and Claude Code sessions
  settings            open the settings page
  update <id> <file>  update a keypad's firmware
  hook <Event>        (used by Claude Code)
  mcp                 (used by Claude Code)
  version
`)
}

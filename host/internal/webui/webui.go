// Package webui embeds the settings page served on loopback by the agent.
package webui

import "embed"

//go:embed static
var Files embed.FS

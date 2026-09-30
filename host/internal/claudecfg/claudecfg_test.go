package claudecfg

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestInstallUninstallPreservesOtherSettings(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("CLAUDE_CONFIG_DIR", dir)
	FindClaude = func() string { return "" } // edit .claude.json directly
	orig := `{"model":"opus","env":{"FOO":"1"},"hooks":{"Stop":[{"hooks":[{"type":"command","command":"/usr/bin/other"}]}]}}`
	if err := os.WriteFile(filepath.Join(dir, "settings.json"), []byte(orig), 0o600); err != nil {
		t.Fatal(err)
	}
	bin := "/Applications/Keypad.app/Contents/MacOS/keypad"
	if err := Install(bin, 20); err != nil {
		t.Fatal(err)
	}
	if err := Install(bin, 20); err != nil { // idempotent
		t.Fatal(err)
	}
	st := Check(bin)
	if !st.Installed() || st.Hooks != len(Events) {
		t.Fatalf("status after install: %+v", st)
	}
	if Check("/other/keypad").Stale != true {
		t.Fatal("stale binary not detected")
	}
	var m map[string]any
	b, _ := os.ReadFile(filepath.Join(dir, "settings.json"))
	_ = json.Unmarshal(b, &m)
	stop := m["hooks"].(map[string]any)["Stop"].([]any)
	if len(stop) != 2 {
		t.Fatalf("foreign Stop hook lost or duplicated: %v", stop)
	}

	if err := Uninstall(); err != nil {
		t.Fatal(err)
	}
	b, _ = os.ReadFile(filepath.Join(dir, "settings.json"))
	m = nil
	_ = json.Unmarshal(b, &m)
	if m["model"] != "opus" || m["env"].(map[string]any)["FOO"] != "1" {
		t.Fatalf("unrelated settings changed: %v", m)
	}
	hooks := m["hooks"].(map[string]any)
	if len(hooks) != 1 || len(hooks["Stop"].([]any)) != 1 {
		t.Fatalf("uninstall left keypad hooks or removed others: %v", hooks)
	}
	if Check(bin).Hooks != 0 || Check(bin).MCP {
		t.Fatal("still installed after uninstall")
	}
}

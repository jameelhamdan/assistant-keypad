package core

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"sync"
	"time"

	"github.com/jameelhamdan/assistant-keypad/host/internal/config"
)

// WorkerEnv marks processes started as workers; the hook shim forwards it.
const WorkerEnv = "KEYPAD_WORKER"

// Workers runs `claude -p` in an idle project when a shortcut is sent to it.
// Workers are ordinary Claude Code sessions: they load the same hooks, so
// their permission prompts come to the keypad. No bypass flag is ever used.
type Workers struct {
	a  *Agent
	mu sync.Mutex
	w  map[int]*worker
}

type worker struct {
	WorkerInfo
	cmd *exec.Cmd
}

type WorkerInfo struct {
	PID     int       `json:"pid"`
	Project string    `json:"project"`
	Label   string    `json:"label"`
	Started time.Time `json:"started"`
	Done    bool      `json:"done"`
	Exit    int       `json:"exit"`
	Result  string    `json:"result"`
}

func NewWorkers(a *Agent) *Workers { return &Workers{a: a, w: map[int]*worker{}} }

func (ws *Workers) List() []WorkerInfo {
	ws.mu.Lock()
	defer ws.mu.Unlock()
	out := []WorkerInfo{}
	for _, w := range ws.w {
		out = append(out, w.WorkerInfo)
	}
	return out
}

func (ws *Workers) running() int {
	n := 0
	for _, w := range ws.w {
		if !w.Done {
			n++
		}
	}
	return n
}

// Start launches a worker in cwd with the shortcut's prompt.
func (ws *Workers) Start(cwd, project string, sc config.Shortcut) error {
	cfg := ws.a.Config().Workers
	ws.mu.Lock()
	if ws.running() >= cfg.MaxParallel {
		ws.mu.Unlock()
		return fmt.Errorf("%d workers already running", cfg.MaxParallel)
	}
	ws.mu.Unlock()
	bin := FindClaude()
	if bin == "" {
		return errors.New("claude executable not found")
	}
	args := []string{"-p", sc.Prompt, "--output-format", "json", "--max-turns", strconv.Itoa(cfg.MaxTurns)}
	if cfg.Model != "" {
		args = append(args, "--model", cfg.Model)
	}
	cmd := exec.Command(bin, args...)
	cmd.Dir = cwd
	cmd.Env = append(os.Environ(), WorkerEnv+"=1")
	hideWindow(cmd)
	logDir := filepath.Join(config.LogDir(), "workers")
	_ = os.MkdirAll(logDir, 0o700)
	logPath := filepath.Join(logDir, time.Now().Format("20060102-150405")+"-"+safeName(project)+".json")
	f, err := os.Create(logPath)
	if err != nil {
		return err
	}
	cmd.Stdout, cmd.Stderr = f, f
	if err := cmd.Start(); err != nil {
		f.Close()
		return err
	}
	w := &worker{WorkerInfo: WorkerInfo{PID: cmd.Process.Pid, Project: project, Label: sc.Label, Started: time.Now()}, cmd: cmd}
	ws.mu.Lock()
	ws.w[w.PID] = w
	ws.mu.Unlock()
	ws.a.Log.Info("worker started", "pid", w.PID, "project", project, "label", sc.Label, "log", logPath)

	go func() {
		err := cmd.Wait()
		f.Close()
		result := ""
		if b, rerr := os.ReadFile(logPath); rerr == nil {
			var out struct {
				Result string `json:"result"`
			}
			if json.Unmarshal(b, &out) == nil {
				result = out.Result
			}
		}
		ws.mu.Lock()
		w.Done, w.Exit, w.Result = true, cmd.ProcessState.ExitCode(), clip(result, 200)
		ws.mu.Unlock()
		ws.a.Log.Info("worker finished", "pid", w.PID, "exit", w.Exit, "err", err)
		level := "ok"
		if w.Exit != 0 {
			level = "warn"
		}
		ws.a.Toast(fmt.Sprintf("Worker done (%s): %s", project, firstNonEmpty(firstLine(result), "exit "+strconv.Itoa(w.Exit))), level, 5000)
	}()
	return nil
}

// Stop terminates a running worker.
func (ws *Workers) Stop(pid int) error {
	ws.mu.Lock()
	w, ok := ws.w[pid]
	ws.mu.Unlock()
	if !ok || w.Done {
		return errors.New("no such worker")
	}
	return w.cmd.Process.Kill()
}

// FindClaude locates the claude CLI; background agents get a minimal PATH,
// so the usual install locations are searched as well.
func FindClaude() string {
	if p, err := exec.LookPath("claude"); err == nil {
		return p
	}
	home, _ := os.UserHomeDir()
	cands := []string{
		filepath.Join(home, ".local", "bin", "claude"),
		filepath.Join(home, ".claude", "local", "claude"),
		"/opt/homebrew/bin/claude", "/usr/local/bin/claude",
	}
	if runtime.GOOS == "windows" {
		cands = []string{
			filepath.Join(home, ".local", "bin", "claude.exe"),
			filepath.Join(os.Getenv("APPDATA"), "npm", "claude.cmd"),
			filepath.Join(os.Getenv("LOCALAPPDATA"), "Programs", "claude-code", "claude.exe"),
		}
	}
	for _, c := range cands {
		if st, err := os.Stat(c); err == nil && !st.IsDir() {
			return c
		}
	}
	return ""
}

func safeName(s string) string {
	out := []rune{}
	for _, r := range s {
		if r == '-' || r == '_' || r == '.' || (r >= '0' && r <= '9') || (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') {
			out = append(out, r)
		}
	}
	if len(out) == 0 {
		return "project"
	}
	return string(out)
}

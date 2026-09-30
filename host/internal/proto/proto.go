// Package proto defines the keypad wire protocol v2 (see proto/PROTOCOL.md).
package proto

import (
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
)

const (
	Version     = 2
	TCPPort     = 7470
	MDNSService = "_ckeypad._tcp"
	USBVendorID = "303A" // Espressif native USB

	MaxHostMsg   = 3800
	MaxDeviceMsg = 1024
	ItemsPerPage = 8
	MaxItems     = 32
)

// Key is a keypad key: 1..8 on the matrix, 0 for the encoder click.
type Key struct {
	Label string `json:"label"`
	Act   string `json:"act"`
	Tone  string `json:"tone,omitempty"`
}

// Keys maps "1".."8" to a binding.
type Keys map[string]Key

// Set binds key n (1..8).
func (k Keys) Set(n int, label, act, tone string) Keys {
	if n >= 1 && n <= 8 {
		k[strconv.Itoa(n)] = Key{Label: label, Act: act, Tone: tone}
	}
	return k
}

type Session struct {
	ID      string `json:"id"`
	Project string `json:"project"`
	State   string `json:"state"`
	Title   string `json:"title"`
	Detail  string `json:"detail,omitempty"`
	Since   int    `json:"since"`
}

// ---- host -> device -------------------------------------------------------

type HelloAck struct {
	T    string `json:"t"`
	V    int    `json:"v"`
	Host string `json:"host"`
	Time int64  `json:"time"`
}

type Settings struct {
	T          string `json:"t"`
	Theme      string `json:"theme"`
	Brightness int    `json:"brightness"`
	Name       string `json:"name"`
}

type Status struct {
	T        string    `json:"t"`
	Sessions []Session `json:"sessions"`
	Sel      string    `json:"sel"`
	Pinned   bool      `json:"pinned"`
	Queue    int       `json:"queue"`
	Paused   bool      `json:"paused"`
	Keys     Keys      `json:"keys,omitempty"`
}

type Screen struct {
	T       string   `json:"t"`
	ID      string   `json:"id"`
	Tpl     string   `json:"tpl"` // prompt | list | multi
	Tone    string   `json:"tone,omitempty"`
	Title   string   `json:"title"`
	Project string   `json:"project,omitempty"`
	Body    string   `json:"body,omitempty"`
	Items   []string `json:"items,omitempty"`
	Keys    Keys     `json:"keys,omitempty"`
	Click   string   `json:"click,omitempty"`
	Timeout int      `json:"timeout,omitempty"`
}

type Close struct {
	T   string `json:"t"`
	ID  string `json:"id"`
	Why string `json:"why,omitempty"`
}

type Toast struct {
	T     string `json:"t"`
	Text  string `json:"text"`
	Level string `json:"level"`
	MS    int    `json:"ms"`
}

type Provision struct {
	T    string `json:"t"`
	SSID string `json:"ssid"`
	Pass string `json:"pass"`
	Host string `json:"host"`
	Key  string `json:"key"`
	Name string `json:"name"`
}

type OTABegin struct {
	T    string `json:"t"`
	Size int    `json:"size"`
	MD5  string `json:"md5"`
}

type OTAData struct {
	T   string `json:"t"`
	Off int    `json:"off"`
	D   []byte `json:"d"` // base64 in JSON
}

// Simple is any message that carries only its type (who, ping, unpair, ota_end).
type Simple struct {
	T string `json:"t"`
}

// ---- device -> host -------------------------------------------------------

type WiFi struct {
	State string `json:"state"` // off | connecting | up | fail
	SSID  string `json:"ssid,omitempty"`
	IP    string `json:"ip,omitempty"`
	RSSI  int    `json:"rssi,omitempty"`
}

// In is the union of every device -> host message; unused fields stay zero.
type In struct {
	T      string `json:"t"`
	V      int    `json:"v,omitempty"`
	ID     string `json:"id,omitempty"`
	FW     string `json:"fw,omitempty"`
	Name   string `json:"name,omitempty"`
	Paired bool   `json:"paired,omitempty"`
	Link   string `json:"link,omitempty"`
	WiFi   *WiFi  `json:"wifi,omitempty"`
	Bat    int    `json:"bat,omitempty"`

	Key int    `json:"key,omitempty"`
	Act string `json:"act,omitempty"`
	Idx *int   `json:"idx,omitempty"`
	Sel []int  `json:"sel,omitempty"`
	SID string `json:"sid,omitempty"`

	// wifi report (flattened) / provisioned / ota
	State string `json:"state,omitempty"`
	SSID  string `json:"ssid,omitempty"`
	IP    string `json:"ip,omitempty"`
	RSSI  int    `json:"rssi,omitempty"`
	OK    *bool  `json:"ok,omitempty"`
	Off   int    `json:"off,omitempty"`
	Err   string `json:"err,omitempty"`

	Level string `json:"level,omitempty"`
	Msg   string `json:"msg,omitempty"`
}

var deviceTypes = map[string]bool{
	"hello": true, "pong": true, "ack": true, "press": true, "session": true,
	"provisioned": true, "wifi": true, "ota": true, "log": true,
}

// Encode marshals a host message and enforces the size limit.
func Encode(msg any) ([]byte, error) {
	b, err := json.Marshal(msg)
	if err != nil {
		return nil, err
	}
	if len(b) > MaxHostMsg {
		return nil, fmt.Errorf("message too large (%d bytes)", len(b))
	}
	return b, nil
}

var ErrInvalid = errors.New("invalid device message")

// Decode parses and validates a device message.
func Decode(b []byte) (In, error) {
	var m In
	if len(b) == 0 || len(b) > MaxDeviceMsg {
		return m, ErrInvalid
	}
	if err := json.Unmarshal(b, &m); err != nil || !deviceTypes[m.T] {
		return m, ErrInvalid
	}
	if len(m.ID) > 48 || len(m.Act) > 24 || len(m.SID) > 64 || m.Key < 0 || m.Key > 8 || len(m.Sel) > MaxItems {
		return m, ErrInvalid
	}
	return m, nil
}

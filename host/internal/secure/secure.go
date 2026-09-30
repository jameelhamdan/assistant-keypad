// Package secure implements the paired Wi-Fi channel: a nonce handshake,
// HKDF-SHA256 session keys and AES-256-GCM frames with implicit counters.
package secure

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/hkdf"
	"crypto/rand"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"sync"
)

const (
	KeySize   = 32
	NonceSize = 16
	MaxFrame  = 4096
	info      = "keypad v2"
)

// NewKey returns a fresh random pairing key as hex.
func NewKey() string {
	b := make([]byte, KeySize)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b)
}

// DeriveKeys returns (host->device, device->host) session keys.
func DeriveKeys(psk, nonceHost, nonceDevice []byte) (h2d, d2h []byte, err error) {
	salt := append(append([]byte{}, nonceHost...), nonceDevice...)
	okm, err := hkdf.Key(sha256.New, psk, salt, info, 2*KeySize)
	if err != nil {
		return nil, nil, err
	}
	return okm[:KeySize], okm[KeySize:], nil
}

// ReadFrame reads one u16-length-prefixed frame.
func ReadFrame(r io.Reader) ([]byte, error) {
	var hdr [2]byte
	if _, err := io.ReadFull(r, hdr[:]); err != nil {
		return nil, err
	}
	n := int(binary.BigEndian.Uint16(hdr[:]))
	if n == 0 || n > MaxFrame {
		return nil, fmt.Errorf("bad frame length %d", n)
	}
	b := make([]byte, n)
	_, err := io.ReadFull(r, b)
	return b, err
}

// WriteFrame writes one u16-length-prefixed frame.
func WriteFrame(w io.Writer, b []byte) error {
	if len(b) == 0 || len(b) > MaxFrame {
		return fmt.Errorf("bad frame length %d", len(b))
	}
	buf := make([]byte, 2+len(b))
	binary.BigEndian.PutUint16(buf, uint16(len(b)))
	copy(buf[2:], b)
	_, err := w.Write(buf)
	return err
}

type sealer struct {
	aead cipher.AEAD
	ctr  uint64
}

func newSealer(key []byte) (*sealer, error) {
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	return &sealer{aead: aead}, nil
}

func (s *sealer) nonce() []byte {
	n := make([]byte, 12)
	binary.BigEndian.PutUint64(n[4:], s.ctr)
	s.ctr++
	return n
}

// Conn is an authenticated, encrypted message stream.
type Conn struct {
	rw     io.ReadWriter
	wmu    sync.Mutex
	tx, rx *sealer
	PeerID string
}

// Send encrypts and writes one message.
func (c *Conn) Send(plain []byte) error {
	c.wmu.Lock()
	defer c.wmu.Unlock()
	return WriteFrame(c.rw, c.tx.aead.Seal(nil, c.tx.nonce(), plain, nil))
}

// Recv reads and decrypts one message. Any failure is fatal for the session.
func (c *Conn) Recv() ([]byte, error) {
	f, err := ReadFrame(c.rw)
	if err != nil {
		return nil, err
	}
	p, err := c.rx.aead.Open(nil, c.rx.nonce(), f, nil)
	if err != nil {
		return nil, errors.New("frame authentication failed")
	}
	return p, nil
}

type hi struct {
	T    string `json:"t"`
	V    int    `json:"v"`
	Host string `json:"host,omitempty"`
	ID   string `json:"id,omitempty"`
	N    string `json:"n,omitempty"`
	Why  string `json:"why,omitempty"`
}

// ClientHandshake runs the host side of the handshake over rw.
// wantID is the keypad id we paired with; pskHex its key.
func ClientHandshake(rw io.ReadWriter, hostID, wantID, pskHex string) (*Conn, error) {
	psk, err := hex.DecodeString(pskHex)
	if err != nil || len(psk) != KeySize {
		return nil, errors.New("invalid pairing key")
	}
	nh := make([]byte, NonceSize)
	if _, err := rand.Read(nh); err != nil {
		return nil, err
	}
	out, _ := json.Marshal(hi{T: "hi", V: 2, Host: hostID, N: hex.EncodeToString(nh)})
	if err := WriteFrame(rw, out); err != nil {
		return nil, err
	}
	b, err := ReadFrame(rw)
	if err != nil {
		return nil, err
	}
	var h hi
	if err := json.Unmarshal(b, &h); err != nil {
		return nil, err
	}
	if h.T == "no" {
		return nil, fmt.Errorf("keypad refused: %s", h.Why)
	}
	if h.T != "hi" || h.V != 2 || h.ID != wantID {
		return nil, fmt.Errorf("unexpected keypad %q", h.ID)
	}
	nd, err := hex.DecodeString(h.N)
	if err != nil || len(nd) != NonceSize {
		return nil, errors.New("bad keypad nonce")
	}
	h2d, d2h, err := DeriveKeys(psk, nh, nd)
	if err != nil {
		return nil, err
	}
	return newConn(rw, h2d, d2h, h.ID)
}

// ServerHandshake runs the keypad side; used by the fake device and tests.
func ServerHandshake(rw io.ReadWriter, id, pairedHost, pskHex string) (*Conn, error) {
	b, err := ReadFrame(rw)
	if err != nil {
		return nil, err
	}
	var h hi
	if err := json.Unmarshal(b, &h); err != nil || h.T != "hi" || h.V != 2 {
		return nil, errors.New("bad hello")
	}
	if h.Host != pairedHost {
		out, _ := json.Marshal(hi{T: "no", Why: "not paired with this host"})
		_ = WriteFrame(rw, out)
		return nil, errors.New("unknown host")
	}
	nh, err := hex.DecodeString(h.N)
	if err != nil || len(nh) != NonceSize {
		return nil, errors.New("bad host nonce")
	}
	psk, _ := hex.DecodeString(pskHex)
	nd := make([]byte, NonceSize)
	if _, err := rand.Read(nd); err != nil {
		return nil, err
	}
	out, _ := json.Marshal(hi{T: "hi", V: 2, ID: id, N: hex.EncodeToString(nd)})
	if err := WriteFrame(rw, out); err != nil {
		return nil, err
	}
	h2d, d2h, err := DeriveKeys(psk, nh, nd)
	if err != nil {
		return nil, err
	}
	return newConn(rw, d2h, h2d, h.Host)
}

func newConn(rw io.ReadWriter, txKey, rxKey []byte, peer string) (*Conn, error) {
	tx, err := newSealer(txKey)
	if err != nil {
		return nil, err
	}
	rx, err := newSealer(rxKey)
	if err != nil {
		return nil, err
	}
	return &Conn{rw: rw, tx: tx, rx: rx, PeerID: peer}, nil
}

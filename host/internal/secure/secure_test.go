package secure

import (
	"encoding/hex"
	"net"
	"testing"
)

func pair(t *testing.T, hostKey, devKey, hostID, pairedHost string) (*Conn, *Conn, error, error) {
	t.Helper()
	a, b := net.Pipe()
	t.Cleanup(func() { a.Close(); b.Close() })
	type res struct {
		c   *Conn
		err error
	}
	ch := make(chan res, 1)
	go func() {
		c, err := ServerHandshake(b, "kp-000001", pairedHost, devKey)
		if err != nil {
			b.Close()
		}
		ch <- res{c, err}
	}()
	hc, herr := ClientHandshake(a, hostID, "kp-000001", hostKey)
	if herr != nil {
		a.Close()
	}
	r := <-ch
	return hc, r.c, herr, r.err
}

func TestRoundTrip(t *testing.T) {
	k := NewKey()
	h, d, err1, err2 := pair(t, k, k, "h-1", "h-1")
	if err1 != nil || err2 != nil {
		t.Fatal(err1, err2)
	}
	for _, msg := range []string{`{"t":"ping"}`, `{"t":"status"}`} {
		go h.Send([]byte(msg))
		got, err := d.Recv()
		if err != nil || string(got) != msg {
			t.Fatalf("h->d %q %v", got, err)
		}
		go d.Send([]byte(msg))
		got, err = h.Recv()
		if err != nil || string(got) != msg {
			t.Fatalf("d->h %q %v", got, err)
		}
	}
}

func TestWrongKeyFailsFirstFrame(t *testing.T) {
	h, d, err1, err2 := pair(t, NewKey(), NewKey(), "h-1", "h-1")
	if err1 != nil || err2 != nil {
		t.Fatal(err1, err2)
	}
	go h.Send([]byte(`{"t":"ping"}`))
	if _, err := d.Recv(); err == nil {
		t.Fatal("frame with the wrong key was accepted")
	}
}

func TestUnknownHostRefused(t *testing.T) {
	k := NewKey()
	_, _, err1, err2 := pair(t, k, k, "h-stranger", "h-1")
	if err1 == nil || err2 == nil {
		t.Fatal("stranger host was accepted")
	}
}

func TestReplayRejected(t *testing.T) {
	// A frame sealed with counter 0 cannot be accepted a second time.
	k, _ := hex.DecodeString(NewKey())
	s, _ := newSealer(k)
	r, _ := newSealer(k)
	f := s.aead.Seal(nil, s.nonce(), []byte("x"), nil)
	if _, err := r.aead.Open(nil, r.nonce(), f, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := r.aead.Open(nil, r.nonce(), f, nil); err == nil {
		t.Fatal("replayed frame accepted")
	}
}

// Fixed vector shared with the firmware (firmware/test/test_crypto).
func TestKnownVector(t *testing.T) {
	psk, _ := hex.DecodeString("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")
	nh, _ := hex.DecodeString("a0a1a2a3a4a5a6a7a8a9aaabacadaeaf")
	nd, _ := hex.DecodeString("b0b1b2b3b4b5b6b7b8b9babbbcbdbebf")
	h2d, d2h, err := DeriveKeys(psk, nh, nd)
	if err != nil {
		t.Fatal(err)
	}
	s, _ := newSealer(h2d)
	ct := s.aead.Seal(nil, s.nonce(), []byte(`{"t":"ping"}`), nil)
	if hex.EncodeToString(h2d) != "e25da196c947aae7c4d9a63fa310a0d1b128ab8590e4137ad616b02ac49aec9e" ||
		hex.EncodeToString(d2h) != "5dcab987978d57b78a92922eb6e095892bf3bc4056d9d94828ac507e5b7a3492" ||
		hex.EncodeToString(ct) != "afab167107d196ed524c879b0e8812465b0b6bde0e2df6c0be57ca88" {
		t.Fatalf("vector changed: h2d=%x d2h=%x ct=%x", h2d, d2h, ct)
	}
}

package main

import (
	"bufio"
	"crypto/sha1"
	"encoding/base64"
	"encoding/binary"
	"errors"
	"io"
	"net"
	"net/http"
	"strings"
	"sync"
)

// Минимальный WebSocket (RFC 6455) без внешних библиотек: одна сторона — сервер, кадры клиента маскированы.
const (
	opCont   = 0x0
	opText   = 0x1
	opBinary = 0x2
	opClose  = 0x8
	opPing   = 0x9
	opPong   = 0xA
	maxFrame = 16 << 20
)

type wsConn struct {
	c   net.Conn
	br  *bufio.Reader
	wmu sync.Mutex
}

func upgrade(w http.ResponseWriter, r *http.Request) (*wsConn, error) {
	if !strings.EqualFold(r.Header.Get("Upgrade"), "websocket") || r.Header.Get("Sec-WebSocket-Key") == "" {
		return nil, errors.New("нужен WebSocket")
	}
	hj, ok := w.(http.Hijacker)
	if !ok {
		return nil, errors.New("соединение нельзя перехватить")
	}
	c, rw, err := hj.Hijack()
	if err != nil {
		return nil, err
	}
	h := sha1.Sum([]byte(r.Header.Get("Sec-WebSocket-Key") + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"))
	resp := "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n" +
		"Sec-WebSocket-Accept: " + base64.StdEncoding.EncodeToString(h[:]) + "\r\n\r\n"
	if _, err := c.Write([]byte(resp)); err != nil {
		c.Close()
		return nil, err
	}
	return &wsConn{c: c, br: rw.Reader}, nil
}

// read — следующее сообщение (склеивает фрагменты, сам отвечает на ping). io.EOF — соединение закрыто.
func (ws *wsConn) read() (op byte, data []byte, err error) {
	var msg []byte
	var msgOp byte
	for {
		var hdr [2]byte
		if _, err = io.ReadFull(ws.br, hdr[:]); err != nil {
			return 0, nil, err
		}
		fin, opcode, masked := hdr[0]&0x80 != 0, hdr[0]&0x0F, hdr[1]&0x80 != 0
		n := uint64(hdr[1] & 0x7F)
		switch n {
		case 126:
			var b [2]byte
			if _, err = io.ReadFull(ws.br, b[:]); err != nil {
				return 0, nil, err
			}
			n = uint64(binary.BigEndian.Uint16(b[:]))
		case 127:
			var b [8]byte
			if _, err = io.ReadFull(ws.br, b[:]); err != nil {
				return 0, nil, err
			}
			n = binary.BigEndian.Uint64(b[:])
		}
		if n > maxFrame || len(msg)+int(n) > maxFrame || !masked {
			return 0, nil, errors.New("неверный кадр WebSocket")
		}
		var mask [4]byte
		if _, err = io.ReadFull(ws.br, mask[:]); err != nil {
			return 0, nil, err
		}
		payload := make([]byte, n)
		if _, err = io.ReadFull(ws.br, payload); err != nil {
			return 0, nil, err
		}
		for i := range payload {
			payload[i] ^= mask[i%4]
		}
		switch opcode {
		case opClose:
			_ = ws.write(opClose, nil)
			return 0, nil, io.EOF
		case opPing:
			if err = ws.write(opPong, payload); err != nil {
				return 0, nil, err
			}
			continue
		case opPong:
			continue
		case opText, opBinary:
			msgOp, msg = opcode, payload
		case opCont:
			msg = append(msg, payload...)
		default:
			return 0, nil, errors.New("неизвестный кадр WebSocket")
		}
		if fin {
			return msgOp, msg, nil
		}
	}
}

func (ws *wsConn) write(op byte, data []byte) error {
	ws.wmu.Lock()
	defer ws.wmu.Unlock()
	hdr := []byte{0x80 | op}
	switch n := len(data); {
	case n < 126:
		hdr = append(hdr, byte(n))
	case n <= 0xFFFF:
		hdr = append(hdr, 126, byte(n>>8), byte(n))
	default:
		hdr = append(hdr, 127)
		hdr = binary.BigEndian.AppendUint64(hdr, uint64(n))
	}
	if _, err := ws.c.Write(append(hdr, data...)); err != nil {
		return err
	}
	return nil
}

func (ws *wsConn) close() { ws.c.Close() }

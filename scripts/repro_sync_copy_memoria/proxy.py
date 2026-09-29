"""Proxy TCP que limita a vazão cliente->servidor (bytes/s). rate<=0 = sem limite."""
import socket, sys, threading, time
LISTEN, TARGET_H, TARGET_P, RATE = int(sys.argv[1]), sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
def pipe_down(a, b):
    try:
        while (d := a.recv(65536)): b.sendall(d)
    except OSError: pass
    finally:
        try: b.shutdown(socket.SHUT_WR)
        except OSError: pass
def pipe_up(a, b):
    t0, sent = time.monotonic(), 0
    try:
        while (d := a.recv(16384)):
            if RATE > 0:
                sent += len(d)
                lag = sent / RATE - (time.monotonic() - t0)
                if lag > 0: time.sleep(lag)
            b.sendall(d)
    except OSError: pass
    finally:
        try: b.shutdown(socket.SHUT_WR)
        except OSError: pass
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", LISTEN)); s.listen(8)
while True:
    c, _ = s.accept(); u = socket.create_connection((TARGET_H, TARGET_P))
    threading.Thread(target=pipe_up, args=(c, u), daemon=True).start()
    threading.Thread(target=pipe_down, args=(u, c), daemon=True).start()

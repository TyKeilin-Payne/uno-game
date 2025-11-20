# broker.py
import socket
import threading
import time

# Broker settings
BROKER_HOST = "0.0.0.0"
BROKER_PORT = 6000   # clients and workers connect here for registration/assignment
LOCK = threading.Lock()

# Worker info structure: { worker_id: {"host": host, "port": port, "load": int, "last_seen": timestamp} }
workers = {}
next_worker_id = 1

def handle_connection(conn, addr):
    """
    Protocol (text lines):
    Worker registration: "WORKER_REGISTER <host> <port>"
    Client request: "CLIENT_REQ <room_name_or_auto>"
    """
    try:
        data = conn.recv(1024).decode().strip()
        if not data:
            conn.close()
            return

        parts = data.split()
        if parts[0] == "WORKER_REGISTER":
            # Worker registering itself
            if len(parts) < 3:
                conn.sendall("ERROR Missing args\n".encode())
                conn.close()
                return
            host = parts[1]
            port = int(parts[2])
            global next_worker_id
            with LOCK:
                worker_id = f"worker-{next_worker_id}"
                next_worker_id += 1
                workers[worker_id] = {"host": host, "port": port, "load": 0, "last_seen": time.time()}
            print(f"[BROKER] Registered {worker_id} -> {host}:{port}")
            conn.sendall(f"OK_REGISTERED {worker_id}\n".encode())

        elif parts[0] == "CLIENT_REQ":
            # client asking for a worker assignment
            desired_room = parts[1] if len(parts) > 1 else "auto"
            with LOCK:
                # remove stale workers older than 60s (simple heartbeat cleanup)
                now = time.time()
                for wid in list(workers.keys()):
                    if now - workers[wid]["last_seen"] > 120:
                        print(f"[BROKER] Removing stale worker {wid}")
                        workers.pop(wid, None)

                if not workers:
                    conn.sendall("NO_WORKERS\n".encode())
                    conn.close()
                    return
                # pick least-loaded worker
                chosen = min(workers.items(), key=lambda kv: kv[1]["load"])[0]
                workers[chosen]["load"] += 1
                workers[chosen]["last_seen"] = time.time()

                host = workers[chosen]["host"]
                port = workers[chosen]["port"]

            print(f"[BROKER] Assigned client {addr} to {chosen} ({host}:{port}) for room '{desired_room}'")
            # reply: ASSIGN <host> <port> <roomname>
            conn.sendall(f"ASSIGN {host} {port} {desired_room}\n".encode())

        elif parts[0] == "WORKER_HEARTBEAT":
            # keep worker alive (optional)
            worker_id = parts[1] if len(parts) > 1 else None
            with LOCK:
                if worker_id in workers:
                    workers[worker_id]["last_seen"] = time.time()
                    conn.sendall("OK\n".encode())
                else:
                    conn.sendall("UNKNOWN_WORKER\n".encode())
        else:
            conn.sendall("ERROR Unknown command\n".encode())

    except Exception as e:
        print(f"[BROKER] connection error: {e}")
    finally:
        conn.close()

def server_loop():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((BROKER_HOST, BROKER_PORT))
    s.listen(100)
    print(f"[BROKER] Listening on {BROKER_HOST}:{BROKER_PORT}")
    while True:
        conn, addr = s.accept()
        threading.Thread(target=handle_connection, args=(conn, addr), daemon=True).start()

if __name__ == "__main__":
    server_loop()

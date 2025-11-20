# client.py
import socket
import threading

def listen_worker(sock):
    while True:
        try:
            data = sock.recv(4096).decode()
            if not data:
                print("[DISCONNECTED FROM WORKER]")
                break
            print(data, end="")
        except Exception:
            break

def request_assignment(broker_host, broker_port, desired_room="auto"):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((broker_host, broker_port))
        s.sendall(f"CLIENT_REQ {desired_room}\n".encode())
        resp = s.recv(1024).decode().strip()
        s.close()
        if resp.startswith("ASSIGN"):
            _, host, port, room = resp.split(maxsplit=3)
            return host, int(port), room
        elif resp == "NO_WORKERS":
            print("Broker reports no workers available.")
            return None
        else:
            print("Broker response:", resp)
            return None
    except Exception as e:
        print("Error contacting broker:", e)
        return None

def run_client(broker_host="127.0.0.1", broker_port=6000):
    room = input("Enter desired room name (or press Enter for auto): ").strip() or "auto"
    assigned = request_assignment(broker_host, broker_port, room)
    if not assigned:
        print("No assignment received. Exiting.")
        return
    host, port, room_assigned = assigned
    print(f"Connecting to worker {host}:{port} for room '{room_assigned}' ...")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((host, port))
    # start listener
    t = threading.Thread(target=listen_worker, args=(s,), daemon=True)
    t.start()
    try:
        while True:
            msg = input("> ").strip()
            if not msg:
                continue
            s.sendall(msg.encode())
            if msg.lower() == "exit":
                break
    except KeyboardInterrupt:
        print("Interrupted")
    finally:
        s.close()

if __name__ == "__main__":
    broker = input("Enter broker IP (press Enter for localhost): ").strip() or "127.0.0.1"
    port = input("Enter broker port (press Enter for 6000): ").strip()
    port = int(port) if port else 6000
    run_client(broker, port)

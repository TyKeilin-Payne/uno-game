import socket
import threading
import argparse
import time
import random
import socket as pysock
from collections import deque

DEFAULT_WORKER_PORT = 5555
BROKER_TIMEOUT = 2

def get_local_ip():
    s = pysock.socket(pysock.AF_INET, pysock.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

class Deck:
    def __init__(self):
        suits = ['Red', 'Yellow', 'Green', 'Blue']
        values = ['0','1','2','3','4','5','6','7','8','9','Skip','Reverse','Draw Two']
        self.cards = [f"{v} {s}" for s in suits for v in values]
        self.cards.extend(['Wild','Wild Draw Four'] * 4)
        random.shuffle(self.cards)
    def draw(self):
        return self.cards.pop() if self.cards else None

def client_thread(conn, addr, server_state):
    """Handle a connected player socket."""
    try:
        conn.sendall(f"Welcome to worker {server_state['worker_id']} at {server_state['host_ip']}!\nEnter room name to join: ".encode())
        room_name = conn.recv(1024).decode().strip()
        if not room_name:
            conn.close()
            return
        with server_state['lock']:
            server_state['players'].append(addr_key(addr))
            server_state['hands'][addr_key(addr)] = [server_state['deck'].draw() for _ in range(7)]
        log(server_state, f"Player {addr_key(addr)} joined room '{room_name}'")

        conn.sendall(f"You joined room '{room_name}'. Type 'hand', 'draw', 'play <index> [color]', 'top', 'exit'\n".encode())
        while True:
            data = conn.recv(4096).decode().strip()
            if not data:
                break
            handle_player_command(conn, addr_key(addr), data, server_state)
    except Exception as e:
        log(server_state, f"Error with client {addr}: {e}")
    finally:
        with server_state['lock']:
            if addr_key(addr) in server_state['players']:
                server_state['players'].remove(addr_key(addr))
                server_state['hands'].pop(addr_key(addr), None)
        conn.close()
        log(server_state, f"Client {addr} disconnected")

def addr_key(addr_tuple):
    return f"{addr_tuple[0]}:{addr_tuple[1]}"

def log(state, message):
    
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    print(f"[{ts}] [WORKER {state['worker_id']}] ({state['host_ip']}) {message}")

def handle_player_command(conn, player, command, state):
    cmd = command.strip().split()
    if not cmd:
        return
    verb = cmd[0].lower()
    with state['lock']:
        hand = state['hands'].get(player, [])
        top = state['discard'][-1] if state['discard'] else None
    if verb in ("hand","myhand"):
        with state['lock']:
            cards = state['hands'].get(player, [])
            conn.sendall(("HAND " + " | ".join(cards) + "\n").encode())
        return
    if verb == "top" or verb == "look":
        conn.sendall((f"TOP {top}\n").encode())
        return
    if verb == "draw":
        c = state['deck'].draw()
        if c:
            with state['lock']:
                state['hands'][player].append(c)
            conn.sendall((f"DRAWN {c}\n").encode())
            log(state, f"Player {player} drew {c}")
        else:
            conn.sendall("ERROR No cards left\n".encode())
        return
    if verb == "play":
     with state['lock']:
        hand = state['hands'][player]

        # Turn check
        if current_player(state) != player:
            conn.sendall("ERROR It is NOT your turn.\n".encode())
            return

        if len(cmd) == 2 and cmd[1].isdigit():
            idx = int(cmd[1]) - 1
            if idx < 0 or idx >= len(hand):
                conn.sendall("ERROR Invalid card index.\n".encode())
                return
            card = hand[idx]
            
        else:
            card = parse_card(" ".join(cmd[1:]))
            if card not in hand:
                conn.sendall(f"ERROR You do not have {card}\n".encode())
                return

            idx = hand.index(card)

        top = state['discard'][-1] if state['discard'] else None

        if not can_play(card, top, state['active_color']):
            conn.sendall(f"ERROR cannot play {card} on top of {top}\n".encode())
            return
            
        hand.pop(idx)
        state['discard'].append(card)

        if card.startswith("Wild"):
            if len(cmd) >= 3:
                chosen = cmd[-1].capitalize()
            else:
                chosen = "Red"
            state['active_color'] = chosen
        else:
            state['active_color'] = None

        conn.sendall(f"PLAYED {card}\n".encode())
        broadcast_to_all(state, f"{player} played {card}", exclude=player)

        if len(hand) == 0:
            broadcast_to_all(state, f"🎉 PLAYER {player} WINS THE GAME! 🎉")
            conn.sendall("YOU WIN! 🎉\n".encode())
            state['game_over'] = True
            return

        next_turn(state)
        next_p = current_player(state)
        broadcast_to_all(state, f"It is now {next_p}'s turn.")
    return

    if verb == "exit":
        conn.sendall("Goodbye\n".encode())
        return
    conn.sendall("ERROR Unknown command. Use hand, draw, play, top, exit\n".encode())

def can_play(card, top_card, active_color):
    if not top_card:
        return True
    if card.startswith("Wild"):
        return True
    
    try:
        val, col = card.rsplit(" ",1)
    except:
        return False
    if top_card.startswith("Wild") or active_color:
       
        if active_color:
            return col == active_color
    try:
        top_val, top_col = top_card.rsplit(" ",1)
    except:
        return True
    return col == top_col or val == top_val

def broadcast_to_all(state, text, exclude=None):
   
    with state['lock']:
        for p, c in list(state['conns'].items()):
            if p == exclude:
                continue
            try:
                c.sendall((text + "\n").encode())
            except:
                pass

def register_with_broker(broker_host, broker_port, worker_host, worker_port, worker_id):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((broker_host, broker_port))
        s.sendall(f"WORKER_REGISTER {worker_host} {worker_port}\n".encode())
        resp = s.recv(1024).decode().strip()
        s.close()
        return resp
    except Exception as e:
        return f"ERROR {e}"
def parse_card(text):
    """Convert player text into normalized card name."""
    text = text.strip().title()
    parts = text.split()

    if len(parts) == 1:
        return parts[0]

    if len(parts) == 2:
        return f"{parts[0]} {parts[1]}"

    if len(parts) == 3:
        return f"{parts[0]} {parts[1]} {parts[2]}"

    return text
def current_player(state):
    """Return the player whose turn it is."""
    return state.get("turn_order", [])[state.get("turn_index", 0)]

def next_turn(state):
    """Move to the next player's turn."""
    if "turn_order" not in state:
        return
    state["turn_index"] = (state["turn_index"] + 1) % len(state["turn_order"])
       
    with server_state['lock']:
            if len(server_state['players']) >= 2:
                server_state.setdefault("turn_order", server_state['players'].copy())
                server_state.setdefault("turn_index", 0)

def worker_server(listen_host, listen_port, broker_host, broker_port):
    host_ip = get_local_ip()
    worker_id = f"{host_ip}:{listen_port}"
  
    resp = register_with_broker(broker_host, broker_port, host_ip, listen_port, worker_id)
    print(f"[WORKER] Registered with broker -> {resp}")

    state = {
        "worker_id": worker_id,
        "host_ip": host_ip,
        "port": listen_port,
        "players": [],
        "conns": {},    
        "hands": {},
        "deck": Deck(),
        "discard": [],
        "active_color": None,
        "lock": threading.Lock()
    }

    state['discard'].append(state['deck'].draw())

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR,1)
    s.bind((listen_host, listen_port))
    s.listen(50)
    log(state, f"Listening for players on {listen_host}:{listen_port}")

    while True:
        conn, addr = s.accept()
        with state['lock']:
            state['conns'][addr_key(addr)] = conn
        threading.Thread(target=client_thread, args=(conn, addr, state), daemon=True).start()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--broker-host", default="127.0.0.1", help="Broker IP")
    parser.add_argument("--broker-port", default=6000, type=int, help="Broker port")
    parser.add_argument("--port", default=5555, type=int, help="This worker's listening port for clients")
    args = parser.parse_args()
    try:
        worker_server("0.0.0.0", args.port, args.broker_host, args.broker_port)
    except KeyboardInterrupt:
        print("Worker shutting down")


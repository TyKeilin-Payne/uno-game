import socket
import threading
import multiprocessing
import random
import time
import queue
from collections import deque

class Deck:
    def __init__(self):
        suits = ['Red', 'Yellow', 'Green', 'Blue']
        values = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'Skip', 'Reverse', 'Draw Two']
        self.cards = [f"{v} {s}" for s in suits for v in values]
        self.cards.extend(['Wild', 'Wild Draw Four'] * 4)
        self.shuffle_deck()

    def shuffle_deck(self):
        random.shuffle(self.cards)
        # print("[ROOM] Deck shuffled.")

    def draw_card(self):
        if not self.cards:
            return None
        return self.cards.pop()

def game_room_process(room_name, pipe):
    """
    Runs in a separate process for each room. Manages game state (hands, turn order, discard pile)
    and communicates with the main server via the provided pipe.
    Message protocol (from server -> room):
      ("new_client", addr)
      ("command", addr, text)

    From room -> server:
      ("send", addr, text)        # send text to specific client
      ("broadcast", text)        # broadcast to all clients in the room
      ("debug", text)            # debug/info printed by server
    """
    deck = Deck()
    discard = []  
    players = []  
    hands = {}    
    turn_index = 0
    direction = 1  
    waiting_for_color = None  
    lock = multiprocessing.Lock()

    def broadcast(text):
        pipe.send(("broadcast", room_name, text))

    def send(addr, text):
        pipe.send(("send", room_name, addr, text))

    def deal_initial_cards(addr):
        hand = []
        for _ in range(7):
            c = deck.draw_card()
            if c:
                hand.append(c)
        hands[addr] = hand

    def start_match_if_ready():
        if len(players) >= 2 and not discard:
            
            while True:
                top = deck.draw_card()
                if not top:
                    break

                discard.append(top)
                break
            broadcast(f"[ROOM {room_name}] Match started. Top of discard: {discard[-1]}")

            send_turn_notification()

    def send_turn_notification():
        if not players:
            return
        cur = players[turn_index]
        broadcast(f"[ROOM {room_name}] It's {cur}'s turn. Top discard: {discard[-1] if discard else 'None'}")
        send(cur, f"YOUR_TURN")

    def advance_turn(steps=1):
        nonlocal turn_index
        if not players:
            return
        n = len(players)
       
        turn_index = (turn_index + direction * steps) % n
        send_turn_notification()

    def remove_player(addr):
        nonlocal turn_index
        if addr in players:
            idx = players.index(addr)
            players.remove(addr)
            hands.pop(addr, None)
          
            if idx < turn_index or turn_index >= len(players):
                turn_index = max(0, turn_index - 1)

    def card_matches(card, top_card, current_color=None):
        """
        Simple matching:
        - If card is 'Wild' or 'Wild Draw Four' -> always playable (but in real rules there are restrictions)
        - Otherwise, parse "<value> <Color>" and compare by value or color
        current_color is the active color after a Wild was played (string like 'Red'), if provided use that as color.
        """
        if not top_card:
            return True
        if card.startswith("Wild"):
            return True

        try:
            val, col = card.rsplit(" ", 1)
        except ValueError:
            return False
        try:
            top_val, top_col = top_card.rsplit(" ", 1)
        except ValueError:
            
            if current_color:
                _, col = card.rsplit(" ", 1)
                return col == current_color
            return True
        
        if top_card.startswith("Wild") or top_card.startswith("Wild Draw Four"):
            if current_color:
                return col == current_color or val == top_val
            else:
                return True
       
        return col == top_col or val == top_val

    active_color = None

    pipe.send(("debug", room_name, f"Room process started."))

    while True:
        while pipe.poll():
            msg = pipe.recv()
            if isinstance(msg, tuple) and msg[0] == "new_client":
                _, addr = msg
                if addr not in players:
                    players.append(addr)
                    deal_initial_cards(addr)
                    broadcast(f"[ROOM {room_name}] Player {addr} joined the room.")
                    send(addr, f"HAND {' | '.join(hands[addr])}")
                    send(addr, f"INFO You have been dealt 7 cards.")
                    start_match_if_ready()

            elif isinstance(msg, tuple) and msg[0] == "command":
                _, addr, command = msg
                command = command.strip()
                if addr not in players:
                    send(addr, "ERROR You are not in this room.")
                    continue

                if addr not in hands:
                    send(addr, "ERROR No hand found.")
                    continue

                if not command:
                    continue

                parts = command.split()
                verb = parts[0].lower()

                if verb in ("hand", "myhand"):
                    send(addr, f"HAND {' | '.join(hands[addr])}")
                    continue
                if verb in ("look", "top", "topcard"):
                    send(addr, f"TOP {discard[-1] if discard else 'None'}")
                    continue
                if verb == "exit":
                    remove_player(addr)
                    broadcast(f"[ROOM {room_name}] Player {addr} left the room.")
                    continue

                if players and players[turn_index] != addr:
                    send(addr, "ERROR Not your turn.")
                    continue

                if verb == "play":
                    if len(parts) < 2:
                        send(addr, "ERROR usage: play <index> [color-for-wild]")
                        continue
                    try:
                        idx = int(parts[1]) - 1
                    except:
                        send(addr, "ERROR index must be a number")
                        continue
                    if idx < 0 or idx >= len(hands[addr]):
                        send(addr, "ERROR invalid card index")
                        continue
                    card = hands[addr][idx]
                    if not card_matches(card, discard[-1] if discard else None, current_color=active_color):
                        send(addr, f"ERROR You cannot play {card} on top of {discard[-1] if discard else 'None'} (active color: {active_color})")
                        continue

                    played = hands[addr].pop(idx)
                    discard.append(played)
                    broadcast(f"[ROOM {room_name}] {addr} played {played}")
                    active_color = None

                    if played.startswith("Wild"):
                        chosen_color = None
                        if len(parts) >= 3:
                            chosen_color = parts[2].capitalize()
                            if chosen_color not in ('Red', 'Green', 'Blue', 'Yellow'):
                                send(addr, "ERROR invalid color choice. Use Red, Green, Blue, Yellow.")
                                continue
                        else:
                            chosen_color = "Red"
                        active_color = chosen_color
                        broadcast(f"[ROOM {room_name}] {addr} chose color {chosen_color}")
                        if played == "Wild Draw Four":
                            advance_turn(0)
                            next_idx = (turn_index + direction) % len(players) if players else None
                            if next_idx is not None:
                                next_player = players[next_idx]
                                drawn = []
                                for _ in range(4):
                                    c = deck.draw_card()
                                    if c:
                                        drawn.append(c)
                                        hands[next_player].append(c)
                                send(next_player, f"DRAWN {' | '.join(drawn)}")
                                broadcast(f"[ROOM {room_name}] {next_player} drew 4 cards (penalty).")
                               
                                advance_turn(2)  
                                continue
                       
                        advance_turn(1)
                        continue
                    if " " in played:
                        val, col = played.rsplit(" ", 1)
                    else:
                        val, col = played, None

                    if val == "Skip":
                        advance_turn(2)
                        continue
                    elif val == "Reverse":
                        if len(players) == 2:
                            advance_turn(2)
                        else:
                            direction *= -1
                            advance_turn(1)
                        continue
                    elif val == "Draw Two":
                        next_idx = (turn_index + direction) % len(players) if players else None
                        if next_idx is not None:
                            next_player = players[next_idx]
                            drawn = []
                            for _ in range(2):
                                c = deck.draw_card()
                                if c:
                                    drawn.append(c)
                                    hands[next_player].append(c)
                            send(next_player, f"DRAWN {' | '.join(drawn)}")
                            broadcast(f"[ROOM {room_name}] {next_player} drew 2 cards (penalty).")
                        advance_turn(2)
                        continue
                    else:
                        advance_turn(1)
                        continue

                elif verb == "draw":
                    c = deck.draw_card()
                    if c:
                        hands[addr].append(c)
                        send(addr, f"DRAWN {c}")
                        broadcast(f"[ROOM {room_name}] {addr} drew a card.")
                    else:
                        send(addr, "ERROR No cards left to draw.")
                    advance_turn(1)
                    continue

                else:
                    send(addr, "ERROR Unknown command. Use play <index> [color], draw, hand, top, exit.")
                    continue

        time.sleep(0.05)

class GameServer:
    def __init__(self, host='0.0.0.0', port=5555):
        self.host = host
        self.port = port
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(10)

        self.rooms = {}         
        self.room_pipes = {}    
        self.room_players = {}  
        self.client_map = {}    
        print(f"[SERVER] Listening on {self.host}:{self.port}")

        self.lock = threading.Lock()

    def start(self):
        print("[SERVER STARTED] Waiting for players...")
        threading.Thread(target=self.listen_for_room_messages, daemon=True).start()
        while True:
            conn, addr = self.server_socket.accept()
            addr_key = f"{addr[0]}:{addr[1]}"
            print(f"[NEW CONNECTION] {addr_key}")
            with self.lock:
                self.client_map[addr_key] = conn
            threading.Thread(target=self.handle_client, args=(conn, addr_key), daemon=True).start()

    def create_game_room(self, room_name):
        parent_conn, child_conn = multiprocessing.Pipe()
        room_process = multiprocessing.Process(
            target=game_room_process,
            args=(room_name, child_conn),
            daemon=True
        )
        room_process.start()
        self.rooms[room_name] = room_process
        self.room_pipes[room_name] = parent_conn
        self.room_players[room_name] = []
        print(f"[SERVER] Created new room process '{room_name}'.")

    def listen_for_room_messages(self):
        """Listens for messages from room processes and forwards them to clients."""
        while True:
            for room_name, pipe in list(self.room_pipes.items()):
                if pipe.poll():
                    msg = pipe.recv()
                   
                    if isinstance(msg, tuple):
                        if msg[0] == "send":
                            _, rn, addr, text = msg
                            self.route_to_client(addr, text)
                        elif msg[0] == "broadcast":
                            _, rn, text = msg
                          
                            with self.lock:
                                for paddr in list(self.room_players.get(rn, [])):
                                    self.route_to_client(paddr, text)
                        elif msg[0] == "debug":
                            _, rn, text = msg
                            print(f"[ROOM DEBUG {rn}] {text}")
                        else:
                            print(f"[IPC {room_name}] {msg}")
                    else:
                        print(f"[IPC {room_name}] {msg}")
            time.sleep(0.02)

    def route_to_client(self, addr, text):
        """Send a message text to a connected client address key (addr is 'ip:port')."""
        with self.lock:
            conn = self.client_map.get(addr)
        if not conn:
            print(f"[ROUTE] client {addr} not connected (can't send: {text})")
            return
        try:
            conn.sendall((text + "\n").encode())
        except Exception as e:
            print(f"[ERROR] sending to {addr}: {e}")

    def handle_client(self, conn, addr_key):
        """Handle the TCP connection on the server side. Acts as relay between TCP client and room process."""
        try:
            conn.sendall("Welcome to UNO Server!\nType a room name to join or 'auto' for automatic assignment: ".encode())
            room_name = conn.recv(1024).decode().strip()
            if not room_name:
                conn.close()
                with self.lock:
                    self.client_map.pop(addr_key, None)
                return

            if room_name.lower() == "auto":
            
                chosen = None
                with self.lock:
                    for rn, players in self.room_players.items():
                        if len(players) < 4:
                            chosen = rn
                            break
                if not chosen:
                    chosen = f"room{len(self.rooms)+1}"
                    self.create_game_room(chosen)
                room_name = chosen

            if room_name not in self.rooms:
                self.create_game_room(room_name)

            with self.lock:
                if addr_key not in self.room_players[room_name]:
                    self.room_players[room_name].append(addr_key)

            self.room_pipes[room_name].send(("new_client", addr_key))
            conn.sendall(f"You joined room '{room_name}'. Use commands: play <index> [color-for-wild], draw, hand, top, exit\n".encode())

            while True:
                data = conn.recv(1024).decode().strip()
                if not data:
                    break
         
                if data.lower() == "exit":
                 
                    self.room_pipes[room_name].send(("command", addr_key, "exit"))
                    break
        
                self.room_pipes[room_name].send(("command", addr_key, data))

            print(f"[DISCONNECT] {addr_key}")
        except ConnectionResetError:
            print(f"[DISCONNECT] {addr_key} connection reset")
        finally:
            conn.close()
            with self.lock:
               
                self.client_map.pop(addr_key, None)
            
                for rn, players in self.room_players.items():
                    if addr_key in players:
                        players.remove(addr_key)

if __name__ == "__main__":
    
    try:
        multiprocessing.set_start_method("spawn")
    except RuntimeError:
        pass

    server = GameServer()
    server.start()


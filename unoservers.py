# server.py
import socket
import threading
import multiprocessing
import random
import time
import queue
from collections import deque

# --------------------------
# Deck and basic utilities
# --------------------------
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

# --------------------------
# Game room process
# --------------------------
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
    discard = []  # discard pile; top is last element
    players = []  # list of addresses in order of joining
    hands = {}    # addr -> list of card strings
    turn_index = 0
    direction = 1  # 1 = clockwise, -1 = counterclockwise
    waiting_for_color = None  # if someone played a Wild, store (addr) expecting color next (not used heavily)
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
            # start the game by putting one card on discard (non-action ideally but we'll accept)
            while True:
                top = deck.draw_card()
                if not top:
                    break
                # Avoid starting with Wild Draw Four (optional) — accept any
                discard.append(top)
                break
            broadcast(f"[ROOM {room_name}] Match started. Top of discard: {discard[-1]}")
            # tell players whose turn it is
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
        # compute next index with direction
        turn_index = (turn_index + direction * steps) % n
        send_turn_notification()

    def remove_player(addr):
        nonlocal turn_index
        if addr in players:
            idx = players.index(addr)
            players.remove(addr)
            hands.pop(addr, None)
            # adjust turn_index if necessary
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
        # parse
        try:
            val, col = card.rsplit(" ", 1)
        except ValueError:
            return False
        try:
            top_val, top_col = top_card.rsplit(" ", 1)
        except ValueError:
            # top might be Wild: then current_color should be provided
            if current_color:
                _, col = card.rsplit(" ", 1)
                return col == current_color
            return True
        # if top is Wild we may have a current_color
        if top_card.startswith("Wild") or top_card.startswith("Wild Draw Four"):
            if current_color:
                return col == current_color or val == top_val
            else:
                return True
        # normal match
        return col == top_col or val == top_val

    # track active color when a Wild is played
    active_color = None

    pipe.send(("debug", room_name, f"Room process started."))

    while True:
        # read commands from server
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
                # ignore cmds from unknown players
                if addr not in players:
                    send(addr, "ERROR You are not in this room.")
                    continue

                # If the player hasn't a hand, something wrong
                if addr not in hands:
                    send(addr, "ERROR No hand found.")
                    continue

                # Only allow actions when it's player's turn (except 'hand' or 'look' or 'exit')
                if not command:
                    continue

                parts = command.split()
                verb = parts[0].lower()

                # Allow some informational commands any time
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

                # enforce turn
                if players and players[turn_index] != addr:
                    send(addr, "ERROR Not your turn.")
                    continue

                # PLAY: 'play <index> [color]' index is 1-based index into player's hand
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
                    # check match against top discard
                    if not card_matches(card, discard[-1] if discard else None, current_color=active_color):
                        send(addr, f"ERROR You cannot play {card} on top of {discard[-1] if discard else 'None'} (active color: {active_color})")
                        continue

                    # remove card from hand and place on discard
                    played = hands[addr].pop(idx)
                    discard.append(played)
                    broadcast(f"[ROOM {room_name}] {addr} played {played}")
                    # reset active_color unless wild sets it
                    active_color = None

                    # Apply simple actions
                    # parse value and color
                    if played.startswith("Wild"):
                        # determine chosen color if provided
                        chosen_color = None
                        if len(parts) >= 3:
                            chosen_color = parts[2].capitalize()
                            if chosen_color not in ('Red', 'Green', 'Blue', 'Yellow'):
                                send(addr, "ERROR invalid color choice. Use Red, Green, Blue, Yellow.")
                                continue
                        else:
                            # if not provided, default to Red (or ask, but keep simple)
                            chosen_color = "Red"
                        active_color = chosen_color
                        broadcast(f"[ROOM {room_name}] {addr} chose color {chosen_color}")
                        if played == "Wild Draw Four":
                            # next player draws 4 and loses turn
                            advance_turn(0)  # do not change now; target is next player
                            # compute next player index
                            next_idx = (turn_index + direction) % len(players) if players else None
                            if next_idx is not None:
                                next_player = players[next_idx]
                                # draw 4
                                drawn = []
                                for _ in range(4):
                                    c = deck.draw_card()
                                    if c:
                                        drawn.append(c)
                                        hands[next_player].append(c)
                                send(next_player, f"DRAWN {' | '.join(drawn)}")
                                broadcast(f"[ROOM {room_name}] {next_player} drew 4 cards (penalty).")
                                # skip that player's turn by advancing 1 extra
                                # since we will advance normally once at end of handling, we advance 1 more now
                                advance_turn(2)  # skip next player
                                continue
                        # if normal Wild just continue and advance one
                        advance_turn(1)
                        continue

                    # Not wild
                    # split value and color
                    if " " in played:
                        val, col = played.rsplit(" ", 1)
                    else:
                        val, col = played, None

                    if val == "Skip":
                        # next player skipped
                        advance_turn(2)
                        continue
                    elif val == "Reverse":
                        if len(players) == 2:
                            # in two-player Reverse acts like Skip
                            advance_turn(2)
                        else:
                            direction *= -1
                            advance_turn(1)
                        continue
                    elif val == "Draw Two":
                        # next player draws 2 and loses turn
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
                        # skip next player's turn
                        advance_turn(2)
                        continue
                    else:
                        # normal number or other action but no extra effect
                        advance_turn(1)
                        continue

                elif verb == "draw":
                    # player draws one card and turn ends (simple rules)
                    c = deck.draw_card()
                    if c:
                        hands[addr].append(c)
                        send(addr, f"DRAWN {c}")
                        broadcast(f"[ROOM {room_name}] {addr} drew a card.")
                    else:
                        send(addr, "ERROR No cards left to draw.")
                    # player's turn ends after drawing
                    advance_turn(1)
                    continue

                else:
                    send(addr, "ERROR Unknown command. Use play <index> [color], draw, hand, top, exit.")
                    continue

        time.sleep(0.05)

# --------------------------
# Server (communication broker)
# --------------------------
class GameServer:
    def __init__(self, host='0.0.0.0', port=5555):
        self.host = host
        self.port = port
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(10)

        # IPC communication for room processes
        self.rooms = {}         # room_name -> Process
        self.room_pipes = {}    # room_name -> parent_conn
        self.room_players = {}  # room_name -> list of addrs
        self.client_map = {}    # addr -> conn

        print(f"[SERVER] Listening on {self.host}:{self.port}")

        # lock for thread-safe maps
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
                    # msg types from room: ("send", room_name, addr, text), ("broadcast", room_name, text), ("debug", ...)
                    if isinstance(msg, tuple):
                        if msg[0] == "send":
                            _, rn, addr, text = msg
                            self.route_to_client(addr, text)
                        elif msg[0] == "broadcast":
                            _, rn, text = msg
                            # send to all players in that room
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

            # Auto-assign logic: find a room with fewer than 4 players or create a new one
            if room_name.lower() == "auto":
                # pick an existing small room or make a new one
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

            # Create room if needed
            if room_name not in self.rooms:
                self.create_game_room(room_name)

            # register player in server's room mapping
            with self.lock:
                if addr_key not in self.room_players[room_name]:
                    self.room_players[room_name].append(addr_key)

            # tell room process about new client
            self.room_pipes[room_name].send(("new_client", addr_key))
            conn.sendall(f"You joined room '{room_name}'. Use commands: play <index> [color-for-wild], draw, hand, top, exit\n".encode())

            # relay loop: read messages from the client and forward to room process
            while True:
                data = conn.recv(1024).decode().strip()
                if not data:
                    break
                # Simple exit handling
                if data.lower() == "exit":
                    # inform room
                    self.room_pipes[room_name].send(("command", addr_key, "exit"))
                    break
                # forward arbitrary command to the room process
                self.room_pipes[room_name].send(("command", addr_key, data))

            print(f"[DISCONNECT] {addr_key}")
        except ConnectionResetError:
            print(f"[DISCONNECT] {addr_key} connection reset")
        finally:
            conn.close()
            with self.lock:
                # remove from client_map
                self.client_map.pop(addr_key, None)
                # remove from room players lists
                for rn, players in self.room_players.items():
                    if addr_key in players:
                        players.remove(addr_key)

if __name__ == "__main__":
    # On some platforms multiprocessing start method matters.
    try:
        multiprocessing.set_start_method("spawn")
    except RuntimeError:
        pass

    server = GameServer()
    server.start()

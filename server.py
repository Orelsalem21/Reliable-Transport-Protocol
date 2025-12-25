import socket
import json

HOST = "localhost"
PORT = 12345
CONFIG_PATH = "server_config.txt"

def send_msg(wfile, obj: str):
    # Send an application-layer message as bytes over a TCP socket
    line = (obj + "\n").encode()
    wfile.write(line)
    wfile.flush()

def recv_msg(rfile):
    # Receive a JSON-encoded message from the TCP socket and decode it into a dictionary
    line = rfile.readline()
    if not line:
        return None
    try:
        return json.loads(line.decode(errors="replace").strip())
    except:
        return {"type": "ERR", "reason": "bad_json"}
    
def load_config(path):

    # Define default configuration values (used if file is missing or invalid)
    cfg = {
        "maximum message size": 400,
        "window_size": 4,
        "dynamic message size": False,
        "timeout": 5,
        "message": ""
    }

    def ask_user():
        # Fallback to user input if config file is missing or invalid
        try:
            cfg["maximum message size"] = int(
                input("Enter maximum message size (default 400): ") or 400
            )
            cfg["window_size"] = int(
                input("Enter window size (default 4): ") or 4
            )
            cfg["dynamic message size"] = (
                input("Dynamic message size? (true/false, default false): ") or "false"
            ).lower() == "true"
        except:
            # If user input is also invalid, keep defaults
            pass

    try:
        # Open configuration file
        f = open(path, "r")

        # Read configuration line by line
        for line in f:
            # Only process valid key:value lines
            if ":" in line:
                # Split only on the first ':' to allow ':' inside values (e.g., file paths)
                key, val = line.split(":", 1)
                key = key.strip()
                val = val.strip().replace('"', '')

                # --- compatibility with assignment format ---
                # Normalize key to support spaces/underscores/case variants
                norm_key = key.strip().lower().replace(" ", "_")

                # Map common variants to the canonical keys used in cfg
                if norm_key in ["maximum_msg_size", "maximum_message_size", "maximum", "mss", "max_msg_size"]:
                    key = "maximum message size"
                elif norm_key in ["window_size", "window", "window_size_", "window-size", "window_size".replace("-", "_")]:
                    key = "window_size"
                elif norm_key in ["dynamic_message_size", "dynamic", "dynamic_msg_size"]:
                    key = "dynamic message size"
                elif norm_key in ["timeout", "time_out"]:
                    key = "timeout"
                elif norm_key in ["message", "message_file", "message_path"]:
                    key = "message"

                # Update configuration according to expected type
                try:
                    if key in ["maximum message size", "window_size", "timeout"]:
                        cfg[key] = int(val)
                    elif key == "dynamic message size":
                        cfg[key] = (val.lower() == "true")
                    elif key == "message":
                        cfg[key] = val
                except:
                    # If a specific line is invalid, ignore it and keep defaults/previous values
                    pass

        f.close()

    except:
        # On any error (file not found, parse error), ask user for input
        ask_user()

    return cfg

def handle_client(conn):
    cfg = load_config(CONFIG_PATH)
    rfile = conn.makefile("rb")
    wfile = conn.makefile("wb")

    # --- Receiver-side state (RDT / TCP-like concepts) ---
    rcv_base = 0                 # next expected Seq (in-order)
    rcv_buffer = {}              # out-of-order buffer (Selective Repeat idea)
    app_data = []                # delivered (reassembled) data

    mss = cfg["maximum message size"]            # MSS / max segment size
    dyn_mss = cfg["dynamic message size"]        # "flow control" variant in assignment

    def send_ack():
        ack_num = rcv_base - 1
        ack = {"type": "ACK", "ack": ack_num}
        if dyn_mss:
            ack["maximum message size"] = mss

        send_msg(wfile, json.dumps(ack))

    try:
        # --- Phase 1: TCP-style 3-way handshake (SYN, SYN/ACK, ACK) ---
        syn = recv_msg(rfile)
        if not syn or syn.get("type") != "SIN":
            return
        send_msg(wfile, json.dumps({"type": "SIN/ACK"}))
        ack = recv_msg(rfile)
        if not ack or ack.get("type") != "ACK":
            return

        # --- Phase 2: Parameter negotiation (MSS + dynamic flag) ---
        req = recv_msg(rfile)
        if req and req.get("type") == "GET_MAX_MSG_SIZE":
            send_msg(wfile, json.dumps({
                "type": "MAX_MSG_SIZE",
                "maximum message size": mss,
                "dynamic message size": dyn_mss
            }))

        # --- Phase 3: Reliable in-order delivery (Seq + receiver buffer + cumulative ACK) ---
        while True:
            seg = recv_msg(rfile)
            if not seg:
                break

            t = seg.get("type")
            if t == "FIN":
                print("\n--- FINAL MESSAGE ---\n" + "".join(app_data) + "\n--------------------\n")
                send_msg(wfile, json.dumps({"type": "FIN_ACK"}))
                break

            if t != "DATA":
                continue

            seq = seg.get("seq")
            payload = seg.get("payload", "")

            # if seq is invalid, just ACK current cumulative state
            if seq is None or not isinstance(seq, int):
                send_ack()
                continue

            # if this is a duplicate/old segment (already delivered), ACK and ignore payload
            if seq < rcv_base:
                send_ack()
                continue


            # enforce MSS (server side constraint)
            if payload is None or len(payload.encode()) > mss or seq is None:
                send_ack()
                continue

            # buffer if within/above base; deliver in-order as soon as possible
            if seq >= rcv_base:
                rcv_buffer[seq] = payload
                while rcv_base in rcv_buffer:
                    app_data.append(rcv_buffer.pop(rcv_base))
                    rcv_base += 1

            # optional dynamic MSS feedback (simple “receiver-side” adaptation)
            if dyn_mss:
                if len(rcv_buffer) > 2:
                    mss = max(20, mss - 20)
                elif not rcv_buffer:
                    mss = min(cfg["maximum message size"], mss + 10)

            send_ack()

    finally:
        rfile.close()
        wfile.close()

def main():
    # TCP server main loop: create server socket, listen, accept connections, delegate to handle_client
    from socket import AF_INET, SOCK_STREAM, SOL_SOCKET, SO_REUSEADDR, socket

    serverSocket = socket(AF_INET, SOCK_STREAM)
    serverSocket.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    serverSocket.bind((HOST, PORT))
    serverSocket.listen(1)
    print(f"The server is ready to receive on {HOST}:{PORT}")

    while True:
        connectionSocket, addr = serverSocket.accept()
        with connectionSocket:
            handle_client(connectionSocket)

if __name__ == "__main__":
    main()

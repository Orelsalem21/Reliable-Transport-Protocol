import socket
import json
import time
import os

# Default connection settings
HOST = "localhost"
PORT = 12345
CONFIG_PATH = "client_config.txt"

def send_msg(wfile, obj: dict):
    """ Helper to send JSON messages framed with newline """
    # Send JSON message followed by newline
    line = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
    wfile.write(line)
    wfile.flush()

def recv_msg(rfile):
    """ Helper to receive and parse JSON messages """
    # Read and decode JSON message
    line = rfile.readline()
    if not line: return None
    try:
        return json.loads(line.decode().strip())
    except:
        return None

def load_config(file_path):
    # set default configuration values for reliable transfer
    config = {"message": "message.txt", "window_size": 4, "timeout": 5.0}
    
    # check if configuration file exists
    if os.path.exists(file_path):
        try:
            # open file for reading text
            with open(file_path, "r") as f:
                for line in f:
                    if ":" in line:
                        # parse key and value from line
                        key, value = line.split(":", 1)
                        key = key.strip().lower()
                        value = value.strip().strip('"')
                        
                        # update config based on key
                        if key == "message": 
                            config["message"] = value
                        elif key == "window_size" or key == "window size": 
                            config["window_size"] = int(value)
                        elif key == "timeout": 
                            config["timeout"] = float(value)
        except Exception as e:
            print(f"Error reading config file: {e}")
            
    else:
        # file not found, fallback to manual user input
        print(f"Config file {file_path} not found. Using manual input:")
        
        # get message filename
        user_msg = input("Enter message file path (default 'message.txt'): ")
        if user_msg: config["message"] = user_msg
            
        # get window size for sliding window protocol
        user_window = input("Enter window size (default 4): ")
        if user_window: config["window_size"] = int(user_window)
            
        # get timeout duration for retransmission
        user_timeout = input("Enter timeout in seconds (default 5.0): ")
        if user_timeout: config["timeout"] = float(user_timeout)
        
    return config

def run_client():
    # load configuration settings
    config = load_config(CONFIG_PATH)

    # read message data from file
    try:
        with open(config["message"], "r") as f:
            file_data = f.read()
    except FileNotFoundError:
        print(f"Error: Message file '{config['message']}' not found.")
        return

    # create TCP socket for server
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_socket:
        try:
            # connect to server
            client_socket.connect((HOST, PORT))
        except ConnectionRefusedError:
            print("Error: Could not connect to server.")
            return

        # set timeout for handshake
        client_socket.settimeout(config["timeout"])
        
        # create file-like objects for reading/writing
        rfile = client_socket.makefile("rb")
        wfile = client_socket.makefile("wb")

        # --- Phase 1: Handshake ---
        # initiate handshake with server
        send_msg(wfile, {"type": "SIN"})
        
        # receive handshake response
        response = recv_msg(rfile)
        if not response or response.get("type") != "SIN/ACK":
            print("Handshake failed")
            return
        
        # complete handshake
        send_msg(wfile, {"type": "ACK"})

        # --- Phase 2: Negotiation ---
        # request maximum message size from server
        send_msg(wfile, {"type": "GET_MAX_MSG_SIZE"})
        
        # receive negotiation response
        neg_response = recv_msg(rfile)
        if not neg_response: return

        # set protocol parameters
        mms = neg_response.get("maximum message size", 400)
        is_dynamic = neg_response.get("dynamic message size", False)
        print(f"Starting transfer. Window Size: {config['window_size']}, Initial MMS: {mms}")

        # --- Phase 3: Data Transfer (Sliding Window) ---
        # initialize window variables
        file_cursor = 0
        sent_packets_buffer = {}
        base = 0
        next_seq_num = 0
        window_size = config["window_size"]
        timer_start = None

        # set short timeout for non-blocking data loop
        client_socket.settimeout(0.1)

        # loop until all data is sent and acknowledged
        while file_cursor < len(file_data) or base < next_seq_num:

            # send new segments if window allows
            while next_seq_num < base + window_size and file_cursor < len(file_data):
                # slice data based on current MMS
                payload = file_data[file_cursor : file_cursor + mms]
                
                # store packet for potential retransmission
                sent_packets_buffer[next_seq_num] = payload
                
                # send data packet
                print(f"[SEND] Seq {next_seq_num} (len: {len(payload)})")
                send_msg(wfile, {"type": "DATA", "seq": next_seq_num, "payload": payload})

                # start timer if this is the first packet in window
                if base == next_seq_num:
                    timer_start = time.time()

                # update cursors
                file_cursor += len(payload)
                next_seq_num += 1

            # check for incoming ACKs
            try:
                ack_msg = recv_msg(rfile)
                if ack_msg and ack_msg.get("type") == "ACK":
                    ack_num = ack_msg.get("ack")
                    
                    # process cumulative acknowledgment
                    if ack_num >= base:
                        print(f"[ACK] Cumulative up to {ack_num}")
                        base = ack_num + 1
                        
                        # restart timer if there are still unacked packets
                        if base < next_seq_num:
                            timer_start = time.time()
                        else:
                            timer_start = None

                        # update dynamic MMS if provided
                        if is_dynamic and "maximum message size" in ack_msg:
                            mms = ack_msg["maximum message size"]
                            
            except (socket.timeout, Exception):
                # continue loop if no ACK received
                pass

            # handle timeout and retransmission
            if timer_start and (time.time() - timer_start > config["timeout"]):
                print(f"[TIMEOUT] Retransmitting window starting from {base}")
                
                # restart timer
                timer_start = time.time()
                
                # retransmit all unacknowledged packets in window
                for seq in range(base, next_seq_num):
                    if seq in sent_packets_buffer:
                        send_msg(wfile, {"type": "DATA", "seq": seq, "payload": sent_packets_buffer[seq]})

        # --- Phase 4: Termination ---
        # revert to long timeout for final handshake
        client_socket.settimeout(config["timeout"])
        
        # send connection termination request
        send_msg(wfile, {"type": "FIN"})
        
        # wait for final acknowledgment
        final_ack = recv_msg(rfile)
        if final_ack and final_ack.get("type") == "FIN_ACK":
            print("Transfer Complete. FIN_ACK received.")

if __name__ == "__main__":
    run_client()
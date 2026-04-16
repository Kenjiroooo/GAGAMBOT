import cv2
import numpy as np
import serial
import time
import threading
from flask import Flask, render_template_string, request, jsonify
from picamera2 import Picamera2

# --- Flask Web Server Setup ---
app = Flask(__name__)
manual_command = 'X'

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Spider Bot Web Control</title>
    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=0">
    <style>
        body { font-family: sans-serif; text-align: center; margin-top: 50px; background-color: #1a1a1a; color: #fff; touch-action: manipulation; }
        .btn { width: 80px; height: 80px; font-size: 24px; margin: 5px; border-radius: 10px; border: none; background-color: #444; color: white; cursor: pointer; user-select: none; }
        .btn:active { background-color: #777; }
        .empty { width: 80px; height: 80px; display: inline-block; margin: 5px; }
        .grid { display: inline-block; }
        h1 { margin-bottom: 30px; font-size: 28px; }
        .status { margin-top: 20px; font-size: 18px; color: #aaa; }
    </style>
</head>
<body>
    <h1>Spider Bot Control</h1>
    <div class="grid">
        <div>
            <div class="empty"></div>
            <button class="btn" onmousedown="sendCommand('f')" onmouseup="sendCommand('X')" onmouseleave="sendCommand('X')" ontouchstart="sendCommand('f')" ontouchend="sendCommand('X')">W</button>
            <div class="empty"></div>
        </div>
        <div>
            <button class="btn" onmousedown="sendCommand('l')" onmouseup="sendCommand('X')" onmouseleave="sendCommand('X')" ontouchstart="sendCommand('l')" ontouchend="sendCommand('X')">A</button>
            <button class="btn" onmousedown="sendCommand('X')" ontouchstart="sendCommand('X')">Stop</button>
            <button class="btn" onmousedown="sendCommand('r')" onmouseup="sendCommand('X')" onmouseleave="sendCommand('X')" ontouchstart="sendCommand('r')" ontouchend="sendCommand('X')">D</button>
        </div>
        <div>
            <div class="empty"></div>
            <button class="btn" onmousedown="sendCommand('b')" onmouseup="sendCommand('X')" onmouseleave="sendCommand('X')" ontouchstart="sendCommand('b')" ontouchend="sendCommand('X')">S</button>
            <div class="empty"></div>
        </div>
    </div>
    <div class="status" id="status">Ready</div>
    
    <script>
        function sendCommand(cmd) {
            fetch('/cmd/' + cmd, { method: 'POST' }).catch(err => console.error(err));
            let cmdName = {'f': 'Forward', 'b': 'Backward', 'l': 'Left', 'r': 'Right', 'X': 'Stopped'}[cmd] || cmd;
            document.getElementById('status').innerText = 'Manual Command: ' + cmdName;
        }
        
        // Prevent default touch behaviors like scrolling or zooming
        document.addEventListener('touchmove', function(event) {
            event.preventDefault();
        }, { passive: false });
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_PAGE)

@app.route('/cmd/<cmd>', methods=['POST'])
def command(cmd):
    global manual_command
    if cmd in ['f', 'b', 'l', 'r', 'X']:
        manual_command = cmd
    return jsonify({"status": "ok"})

def run_flask():
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

# --- Configuration ---
SERIAL_PORT = '/dev/serial0'
BAUD_RATE = 9600

# Blue ping pong ball HSV range
BLUE_LOWER = np.array([100, 100, 100])
BLUE_UPPER = np.array([130, 255, 255])

# Frame dimensions
FRAME_WIDTH = 320
FRAME_HEIGHT = 240

# Control thresholds
CENTER_X = FRAME_WIDTH // 2
TOLERANCE_X = 50
MIN_CONTOUR_AREA = 200
STOP_RADIUS_MAX = 80

# Command limits
LAST_COMMAND = None
COMMAND_COOLDOWN = 0.5
last_sent_time = 0


def send_command(ser, cmd):
    global LAST_COMMAND, last_sent_time
    current_time = time.time()

    if cmd != LAST_COMMAND or (current_time - last_sent_time > COMMAND_COOLDOWN):
        print(f"Sending command: {cmd}")
        ser.write(cmd.encode('utf-8'))
        LAST_COMMAND = cmd
        last_sent_time = current_time


def main():
    print("Starting Web Server on port 5000...")
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    print("Initializing Serial Connection...")
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        time.sleep(2)
        print("Serial Connected!")
    except Exception as e:
        print(f"Error opening serial port: {e}")
        print("Make sure /dev/serial0 is enabled and connected through a logic shifter.")
        return

    print("Initializing Camera...")
    try:
        picam2 = Picamera2()
        config = picam2.create_preview_configuration(
            main={"format": "RGB888", "size": (FRAME_WIDTH, FRAME_HEIGHT)}
        )
        picam2.configure(config)
        picam2.start()
        time.sleep(2)
        print("Camera Connected!")
    except Exception as e:
        print(f"Error opening camera: {e}")
        ser.close()
        return

    print("Tracking Started. Press Ctrl+C to stop.")

    try:
        while True:
            if not ser.is_open:
                try:
                    print("Attempting to reconnect serial...")
                    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
                    time.sleep(1)
                    print("Serial Reconnected!")
                except Exception:
                    time.sleep(1)
                    continue

            frame = picam2.capture_array()

            # Convert RGB (Picamera2) directly to HSV
            hsv = cv2.cvtColor(frame, cv2.COLOR_RGB2HSV)

            mask = cv2.inRange(hsv, BLUE_LOWER, BLUE_UPPER)
            mask = cv2.erode(mask, None, iterations=2)
            mask = cv2.dilate(mask, None, iterations=2)

            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            ball_detected = False

            if len(contours) > 0:
                largest_contour = max(contours, key=cv2.contourArea)

                if cv2.contourArea(largest_contour) > MIN_CONTOUR_AREA:
                    ((x, y), radius) = cv2.minEnclosingCircle(largest_contour)
                    M = cv2.moments(largest_contour)

                    if M["m00"] != 0:
                        center_x = int(M["m10"] / M["m00"])

                        # Draw a square (bounding box) around the detected ball
                        rx, ry, rw, rh = cv2.boundingRect(largest_contour)
                        cv2.rectangle(frame, (rx, ry), (rx+rw, ry+rh), (0, 255, 0), 2) # Green box
                        cv2.circle(frame, (center_x, int(y)), 5, (255, 0, 0), -1)      # Red center dot

                        ball_detected = True

                        if radius > STOP_RADIUS_MAX:
                            target_command = 'X'
                        elif center_x < int(CENTER_X - TOLERANCE_X):
                            target_command = 'l'
                        elif center_x > int(CENTER_X + TOLERANCE_X):
                            target_command = 'r'
                        else:
                            target_command = 'f'

            try:
                if ball_detected:
                    send_command(ser, target_command)
                else:
                    send_command(ser, manual_command)
            except (serial.SerialException, OSError) as e:
                print(f"\n[!] Serial connection lost! Motor brownout? Error: {e}")
                ser.close() # Close to force reopen on next loop
                LAST_COMMAND = None # Reset command state

            # OpenCV video preview (Requires Desktop/VNC!)
            cv2.imshow("Frame", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    except KeyboardInterrupt:
        print("\nProgram stopped by user.")
    finally:
        cv2.destroyAllWindows()
        picam2.stop()
        ser.close()
        print("Resources released.")


if __name__ == "__main__":
    main()

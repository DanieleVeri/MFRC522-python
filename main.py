import RPi.GPIO as GPIO
import MFRC522
import time
import signal
import pickle
from urlparse import urlparse, parse_qs
from multiprocessing import Process, Manager
from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler

actuator_pin = 3

continue_reading = True

def save_state(state):
    with open('state.pickle', 'wb') as handle:
        pickle.dump(state, handle, protocol=pickle.HIGHEST_PROTOCOL)

def load_state():
    try:
        with open('state.pickle', 'rb') as handle:
            s = pickle.load(handle)
    except Exception as e:
        print(e)
        s = {'time': 30}
        for i in range(1,100):
            s["%02d" % (i,)] =  ['', False, 0]
    return s

def rfc_loop(state):
    def end_read(signal,frame):
        global continue_reading
        print("Ctrl+C captured, ending read.")
        continue_reading = False
        GPIO.cleanup()
    signal.signal(signal.SIGINT, end_read)

    global continue_reading
    MIFAREReader = MFRC522.MFRC522()

    # This loop keeps checking for chips. If one is near it will get the UID and authenticate
    while continue_reading:
        
        # Scan for cards    
        (status,TagType) = MIFAREReader.MFRC522_Request(MIFAREReader.PICC_REQIDL)

        # If a card is found
        if status == MIFAREReader.MI_OK:
            print("Card detected")
        
        # Get the UID of the card
        (status,uid) = MIFAREReader.MFRC522_Anticoll()

        # If we have the UID, continue
        if status != MIFAREReader.MI_OK:
            continue

        # Print UID
        print("Card read UID: %s,%s,%s,%s" % (uid[0], uid[1], uid[2], uid[3]))
        
        ## Select the scanned tag
        MIFAREReader.MFRC522_SelectTag(uid)

        # This is the default key for authentication
        key = [0xFF]*6
        # Authenticate
        status = MIFAREReader.MFRC522_Auth(MIFAREReader.PICC_AUTHENT1B, 4, key, uid)

        # Check if authenticated
        if status != MIFAREReader.MI_OK:
            print("Authentication error")
            continue

        data = MIFAREReader.MFRC522_Read(4)
        MIFAREReader.MFRC522_StopCrypto1()

        prefix = ''.join([chr(c) for c in data[7:-4]])
        num = ''.join([chr(c) for c in data[-4:-2]])

        if prefix != 'cpd/v':
            print('prefix error')
            continue
        try:
            if not state[num][1]:
                print('access not enabled for', num)
                continue

            print("enabling access to {} for {} minutes".format(num, state['time']))
            state[num] = [state[num][0], state[num][1], state[num][2]+1]
        except Exception as e:
            print(e)
            continue
def ui(state):
    html = "<form action='/update' method='post'>Durata attivazione: \
        <input type='number' name='time' value='{}'><br/>".format(state['time'])
    html += '<table><tr><th>Numero</th><th>Nome</th><th>Abilitato</th><th>Usi</th></tr>'
    keys = state.keys()
    keys.sort()
    for k in keys:
        if k=='time':
            continue
        html += "<tr>"
        html += "<td>{}</td>\
            <td><input type='text' name='name_{}' value='{}'></td>\
            <td><select name='state_{}'><option value='Si' {}>Si</option><option value='No' {}>No</option></select></td>\
            <td>{}</td>".format(
                k, k, state[k][0], k, 'selected' if state[k][1] else '', 'selected' if not state[k][1] else '', state[k][2])
        html += "</tr>"
    html +="</table><br/>"
    html +="<input type='text' name='reset' value='scrivi \"reset\" e poi Applica per azzerare gli utilizzi'>"
    html += "<input type='submit' value='Applica'></form>"
    return html

class S(BaseHTTPRequestHandler):
    def __init__(self, state, *args):
        self.state = state
        BaseHTTPRequestHandler.__init__(self, *args)

    def _set_headers(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()

    def _html(self, message):
        content = "<html><body>{}</body></html>".format(message)
        return content.encode("utf8")  # NOTE: must return a bytes object!

    def do_GET(self):
        self._set_headers()
        self.wfile.write(self._html(ui(self.state)))

    def do_HEAD(self):
        self._set_headers()

    def do_POST(self):
        self._set_headers()
        content_len = int(self.headers.getheader('content-length', 0))
        post_body = self.rfile.read(content_len)
        body_obj = parse_qs(post_body)
        for k in body_obj.keys():
            o = body_obj[k][0]
            if k == 'time':
                self.state['time'] = int(o)
            if k == 'reset' and o == 'reset':
                for kk in self.state.keys():
                    if kk == 'time':
                        continue
                    self.state[kk] = [self.state[kk][0], self.state[kk][1], 0]
            if k.startswith('name_'):
                num = k[5:]
                self.state[num] = [o, self.state[num][1], self.state[num][2]]
            if k.startswith('state_'):
                num = k[6:]
                self.state[num] = [self.state[num][0], True if o=='Si' else False, self.state[num][2]]
        save_state(dict(self.state))
        prompt = "<script>alert('Aggiornato correttamente')</script>"
        self.wfile.write(self._html(prompt+ui(self.state)))

class http_server:
    def __init__(self, state, host, port):
        def handler(*args):
            S(state, *args)
        server = HTTPServer((host, port), handler)
        print("Starting httpd server on {}:{}".format(host, port))
        server.serve_forever()

def run_server(state):
    http_server(state, '0.0.0.0', 8000)

#GPIO.output(actuator_pin, GPIO.HIGH)
#GPIO.output(actuator_pin, GPIO.LOW)

def main():
    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(actuator_pin, GPIO.OUT)

    s = load_state()
    manager = Manager()
    state = manager.dict(s)

    # Welcome message
    print("Welcome to the MFRC522 data read example")
    print("Press Ctrl-C to stop.")

    rfc_worker = Process(target=rfc_loop, args=(state,))
    http_worker = Process(target=run_server, args=(state,))
    
    rfc_worker.start()
    http_worker.start()

    rfc_worker.join()
    http_worker.join()

if __name__ == '__main__':
    main()
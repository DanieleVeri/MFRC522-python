import RPi.GPIO as GPIO
import MFRC522
import time
import signal
import pickle
from urlparse import parse_qs
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

def rfc_loop(state, current):
    def end_read(signal,frame):
        global continue_reading
        print("Ctrl+C captured, ending read.")
        continue_reading = False
        GPIO.cleanup()
    signal.signal(signal.SIGINT, end_read)

    global continue_reading
    MIFAREReader = MFRC522.MFRC522()

    while continue_reading:
        (status,TagType) = MIFAREReader.MFRC522_Request(MIFAREReader.PICC_REQIDL)
        if status == MIFAREReader.MI_OK:
            print("Card detected")
        
        (status,uid) = MIFAREReader.MFRC522_Anticoll()
        if status != MIFAREReader.MI_OK:
            continue

        print("Card read UID: %s,%s,%s,%s" % (uid[0], uid[1], uid[2], uid[3]))
        MIFAREReader.MFRC522_SelectTag(uid)

        key = [0xFF]*6
        status = MIFAREReader.MFRC522_Auth(MIFAREReader.PICC_AUTHENT1B, 4, key, uid)
        if status != MIFAREReader.MI_OK:
            print("Authentication error")
            continue

        data = MIFAREReader.MFRC522_Read(4)
        MIFAREReader.MFRC522_StopCrypto1()

        prefix = ''.join([chr(c) for c in data[5:-6]])
        num = ''.join([chr(c) for c in data[-6:-4]])

        if prefix != 'cpd/v':
            print('prefix error')
            continue
        try:
            if not state[num][1]:
                print('access not enabled for', num)
                continue

            print("enabling access to {} for {} minutes".format(num, state['time']))
            current['who'] = num
            current['stop'] = int(time.time()) + state['time']*60

        except Exception as e:
            print(e)
            continue

def ui(state):
    html = "<h1>Pannello di controllo</h1><form action='/update' style='width:100%' method='post'>Durata attivazione: \
        <input type='number' name='time' value='{}'><br/>".format(state['time'])
    html += '<table style="width:100%;font-size: 25px;"><tr><th>Numero</th><th>Nome</th><th>Abilitato</th><th>Usi</th></tr>'
    keys = state.keys()
    keys.sort()
    tot=0
    for k in keys:
        if k=='time':
            continue
        tot += state[k][2]
    tot = tot if tot>0 else 1
    for k in keys:
        if k=='time':
            continue
        html += "<tr>"
        html += "<td>{}</td>\
            <td><input style='font-size: 25px;' type='text' name='name_{}' value='{}'></td>\
            <td><select name='state_{}'><option value='Si' {}>Si</option><option value='No' {}>No</option></select></td>\
            <td>{} min ({:.2f}%)</td>".format(
                k, k, state[k][0], k, 'selected' if state[k][1] else '', 'selected' if not state[k][1] else '', state[k][2]//60,
                state[k][2]*100.0/tot)
        html += "</tr>"
    html +="</table><br/>"
    html +="<input type='text' style='width:100%; font-size: 25px;' name='reset' value='scrivi \"reset\" per azzerare gli utilizzi'><br/>"
    html += "<input type='submit' style='width:100%; font-size: 35px;' value='Applica'></form>"
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
        content = "<html><body style='font-size: 25px;'>{}</body></html>".format(message)
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
    http_server(state, '0.0.0.0', 80)

def actuator_loop(state, current):
    def end_read(signal,frame):
        GPIO.cleanup()
    signal.signal(signal.SIGINT, end_read)

    last_save=int(time.time())
    while True:
        now = int(time.time())
        delta = current['stop'] - now
        if delta <= 0:
            GPIO.output(actuator_pin, GPIO.LOW)
        else:
            GPIO.output(actuator_pin, GPIO.HIGH)
            state[current['who']] = [state[current['who']][0], 
                                    state[current['who']][1], 
                                    state[current['who']][2]+1]
        if now - last_save > 60*30:
            print("persisting state")
            save_state(dict(state))
            last_save = now
            
        time.sleep(1)

def main():
    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(actuator_pin, GPIO.OUT)
    GPIO.output(actuator_pin, GPIO.LOW)

    manager = Manager()
    state = manager.dict(load_state())
    current = manager.dict({'who': None, 'stop': 0})

    rfc_worker = Process(target=rfc_loop, args=(state,current,))
    http_worker = Process(target=run_server, args=(state,))
    act_worker = Process(target=actuator_loop, args=(state, current,))
    
    print("Press Ctrl-C to stop.")
    
    rfc_worker.start()
    http_worker.start()
    act_worker.start()

    rfc_worker.join()
    http_worker.join()
    act_worker.join()

if __name__ == '__main__':
    main()

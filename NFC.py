import datetime
import json
import struct
import os
import time
from ftplib import FTP
from tkinter import *
import threading
from io import BytesIO
from smartcard.scard import *
from smartcard.util import toHexString
import smartcard.util
from smartcard.ATR import ATR
from smartcard.CardType import AnyCardType
from smartcard.CardRequest import CardRequest
from smartcard.CardConnectionObserver import CardConnectionObserver

# ===================== NFC READER =====================
VERBOSE = False

BLOCK_NUMBER = 0x04
AUTHENTICATE = [0xFF, 0x88, 0x00, BLOCK_NUMBER, 0x60, 0x00]
GET_UID = [0xFF, 0xCA, 0x00, 0x00, 0x04]
READ_16_BINARY_BLOCKS = [0xFF, 0xB0, 0x00, 0x04, 0x10]
UPDATE_FIXED_BLOCKS = [0xFF, 0xD6, 0x00, BLOCK_NUMBER, 0x10]

class NFC_Reader():
    def __init__(self, uid=""):
        self.uid = uid
        self.hresult, self.hcontext = SCardEstablishContext(SCARD_SCOPE_USER)
        self.hresult, self.readers = SCardListReaders(self.hcontext, [])
        assert len(self.readers) > 0
        self.reader = self.readers[0]
        print("Found reader: " + str(self.reader))

        self.hresult, self.hcard, self.dwActiveProtocol = SCardConnect(
            self.hcontext,
            self.reader,
            SCARD_SHARE_SHARED,
            SCARD_PROTOCOL_T0 | SCARD_PROTOCOL_T1)

    def send_command(self, command):
        print("Sending command...")
        for iteration in range(1):
            try:
                self.hresult, self.response = SCardTransmit(self.hcard, self.dwActiveProtocol, command)
                value = toHexString(self.response)
            except Exception as e:
                print("No Card Found:", e)
                return None, None
        return self.response, value

    def read_uid(self):
        print("Waiting for card...")
        while True:
            try:
                response, uid = self.send_command(GET_UID)
                if response:
                    print("Found!")
                    self.uid = uid
                    return uid.replace(" ", "_")
            except Exception as e:
                pass
    
            try:
                self.hresult, self.hcard, self.dwActiveProtocol = SCardConnect(
                    self.hcontext,
                    self.reader,
                    SCARD_SHARE_SHARED,
                    SCARD_PROTOCOL_T0 | SCARD_PROTOCOL_T1)
            except:
                pass
            time.sleep(1)

# ===================== FTP CONFIG =====================
ftp_host = "127.0.0.1"
ftp_port = 2121
ftp_user = "admin"
ftp_pass = "1234"

# ===================== FTP FUNCTIONS =====================
def generate_and_upload_json(card_id, card_data):
    ftp = None
    try:
        json_content = json.dumps(card_data, indent=4, ensure_ascii=False)
        buffer = BytesIO(json_content.encode('utf-8'))
        filename = f"{card_id}.json"

        ftp = FTP()
        ftp.connect(ftp_host, ftp_port, timeout=5)
        ftp.login(ftp_user, ftp_pass)
        ftp.storbinary(f"STOR {filename}", buffer)
        ftp.quit()
        print(f"Successfully uploaded: {filename}")
    except Exception as e:
        if ftp:
            try: ftp.quit()
            except: pass
        print(f"FTP Upload Error: {e}")

def download_card_data(card_id):
    ftp = None
    target_file = f"{card_id}.json"
    try:
        ftp = FTP()
        ftp.connect(ftp_host, ftp_port, timeout=5)
        ftp.login(ftp_user, ftp_pass)

        buffer = BytesIO()
        ftp.retrbinary(f"RETR {target_file}", buffer.write)
        ftp.quit()

        buffer.seek(0)
        data = json.loads(buffer.read().decode('utf-8'))
        print(f"ดึงข้อมูลใหม่สำเร็จ! Balance: {data.get('balance')}")
        return data
    except Exception as e:
        if ftp:
            try: ftp.quit()
            except: pass
        print(f"FTP Info: {target_file} not found or {e}")
        return None

def update_transaction_log(card_data, entry_point=None, exit_point=None, cost=None):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if exit_point is None:
        card_data["transaction_log"].append({"type": "entry", "time": timestamp, "detail": f"Entered at {entry_point}"})
    else:
        card_data["transaction_log"].append({"type": "exit", "time": timestamp, "detail": f"Exited from {exit_point}, cost {cost}"})
    return card_data

# ===================== CORE LOGIC =====================
def calculate_cost(entry, exit):
    table = {("ด่าน A", "ด่าน B"): 150, ("ด่าน A", "ด่าน C"): 200, ("ด่าน B", "ด่าน C"): 50}
    if entry == exit: return 0
    return table.get((entry, exit)) or table.get((exit, entry), 50)

def update_signal(can_pass):
    if can_pass:
        signal_status.set("PASS")
        signal_label.config(bg="green")
    else:
        signal_status.set("DENIED")
        signal_label.config(bg="red")

def thread_ab():
    """ทุกครั้งที่ Tap Card จะโหลด Balance ใหม่จาก FTP เสมอ"""
    print("--- Start Syncing Data ---")
    card_id = card_id_var.get()
    
    # ดึงข้อมูลใหม่สดๆ จาก FTP
    card_data = download_card_data(card_id)
    if card_data is None:
        update_signal(False)
        balance_var.set("0.00")
        return
    
    # อัปเดตยอดเงินล่าสุดบน GUI
    balance = float(card_data.get("balance", 0))
    balance_var.set(f"{balance:.2f}")

    entry_point = entry_var.get()
    exit_point = exit_var.get()
    can_pass = False

    if mode_var.get() == "entry":
        if balance >= 200: can_pass = True
    else:
        cost = calculate_cost(entry_point, exit_point)
        cost_var.set(str(cost))
        if balance >= cost: can_pass = True

    update_signal(can_pass)

    if can_pass:
        threading.Thread(target=thread_cd, args=(card_data,), daemon=True).start()

def thread_cd(card_data):
    card_id = card_data["card_id"]
    entry_point = entry_var.get()
    exit_point = exit_var.get()
    balance = float(card_data.get("balance", 0))

    if mode_var.get() == "entry":
        update_transaction_log(card_data, entry_point=entry_point)
    else:
        cost = float(cost_var.get())
        card_data["balance"] = balance - cost
        update_transaction_log(card_data, entry_point=entry_point, exit_point=exit_point, cost=cost)
        # แสดงยอดเงินหลังหักทันที
        balance_var.set(f"{card_data['balance']:.2f}")

    generate_and_upload_json(card_id, card_data)
    print("Updated Success!")

def reset_fields():
    # อ่านเลขบัตรใหม่
    card_id = reader.read_uid()
    card_id_var.set(card_id)
    # โหลดข้อมูลใหม่ทันที
    card_data = download_card_data(card_id)
    if card_data:
        balance_var.set(f"{float(card_data.get('balance', 0)):.2f}")
    else:
        balance_var.set("0.00")
    signal_label.config(bg="light grey")
    signal_status.set("READY")

# ===================== GUI SETUP =====================
root = Tk()
root.geometry("1100x600")
root.title("NFC Toll System")
root.configure(bg="seashell2")

card_id_var = StringVar(); entry_var = StringVar(value="ด่าน A")
exit_var = StringVar(value="ด่าน B"); balance_var = StringVar()
cost_var = StringVar(value="0"); signal_status = StringVar(value="READY")
mode_var = StringVar(value="entry")

# UI Frame
f1 = Frame(root, bg="seashell2"); f1.pack(pady=20)

Label(f1, text="Card ID:", font=('TH Sarabun New', 18, 'bold'), bg="seashell2").grid(row=0, column=0)
Label(f1, textvariable=card_id_var, font=('TH Sarabun New', 18), bg="white", width=20).grid(row=0, column=1)

Label(f1, text="Balance:", font=('TH Sarabun New', 18, 'bold'), bg="seashell2").grid(row=0, column=2)
Label(f1, textvariable=balance_var, font=('TH Sarabun New', 18), bg="powder blue", width=16).grid(row=0, column=3)

Label(f1, text="Entry:", font=('TH Sarabun New', 18), bg="seashell2").grid(row=1, column=0)
OptionMenu(f1, entry_var, "ด่าน A", "ด่าน B", "ด่าน C").grid(row=1, column=1)

Label(f1, text="Exit:", font=('TH Sarabun New', 18), bg="seashell2").grid(row=2, column=0)
OptionMenu(f1, exit_var, "ด่าน A", "ด่าน B", "ด่าน C").grid(row=2, column=1)

Label(f1, text="Cost:", font=('TH Sarabun New', 18, 'bold'), bg="seashell2").grid(row=1, column=2)
Label(f1, textvariable=cost_var, font=('TH Sarabun New', 18), bg="powder blue", width=16).grid(row=1, column=3)

Label(f1, text="Signal:", font=('TH Sarabun New', 18, 'bold'), bg="seashell2").grid(row=2, column=2)
signal_label = Label(f1, textvariable=signal_status, font=('TH Sarabun New', 18, 'bold'), width=16, fg="white", bg="light grey")
signal_label.grid(row=2, column=3)

Radiobutton(f1, text="Entry", variable=mode_var, value="entry", font=('TH Sarabun New', 16), bg="seashell2").grid(row=3, column=0)
Radiobutton(f1, text="Exit", variable=mode_var, value="exit", font=('TH Sarabun New', 16), bg="seashell2").grid(row=3, column=1)

Button(f1, text="Tap Card (Sync & Pay)", font=('TH Sarabun New', 16, 'bold'), bg="light green", 
       command=lambda: threading.Thread(target=thread_ab, daemon=True).start()).grid(row=4, column=1, pady=20)
Button(f1, text="Reset Reader", font=('TH Sarabun New', 14), command=reset_fields, bg="orange").grid(row=4, column=2)

# Start NFC Reader
reader = NFC_Reader()
card_id = reader.read_uid()
card_id_var.set(card_id)
# โหลดข้อมูลครั้งแรก
initial_data = download_card_data(card_id)
if initial_data: balance_var.set(f"{initial_data.get('balance'):.2f}")

root.mainloop()
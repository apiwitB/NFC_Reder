import datetime
import json
import struct
import os
import time
import logging
import threading
from ftplib import FTP
from tkinter import *
from io import BytesIO
from smartcard.scard import *
from smartcard.util import toHexString
import smartcard.util
from smartcard.ATR import ATR
from smartcard.CardType import AnyCardType
from smartcard.CardRequest import CardRequest
from smartcard.CardConnectionObserver import CardConnectionObserver

# ===================== LOGGING SETUP =====================
logger = logging.getLogger("NFC_Toll")
logger.setLevel(logging.DEBUG)

_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

_fh = logging.FileHandler("nfc_system.log", encoding="utf-8")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(_fmt)

_ch = logging.StreamHandler()
_ch.setLevel(logging.INFO)
_ch.setFormatter(_fmt)

logger.addHandler(_fh)
logger.addHandler(_ch)

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
        logger.info("Found reader: %s", self.reader)

        self.hresult, self.hcard, self.dwActiveProtocol = SCardConnect(
            self.hcontext,
            self.reader,
            SCARD_SHARE_SHARED,
            SCARD_PROTOCOL_T0 | SCARD_PROTOCOL_T1)

    def send_command(self, command):
        logger.debug("Sending command...")
        try:
            self.hresult, self.response = SCardTransmit(self.hcard, self.dwActiveProtocol, command)
            # แยก Data กับ Status (2 ตัวท้าย)
            data = self.response[:-2]
            status = self.response[-2:]
            return data, status
        except Exception as e:
            logger.debug("Transmit failed: %s", e)
            return None, None

    def read_uid(self):
        logger.info("Waiting for card...")
        while True:
            try:
                data, status = self.send_command(GET_UID)
                # 90 00 (ในรูปแบบ decimal คือ [144, 0]) หมายถึง Success
                if status == [144, 0] and data:
                    # รวม data และ status กลับมาเป็น string เดียวกันเพื่อให้ชื่อโฟลเดอร์ตรงกับของเดิม
                    full_response = data + status
                    uid_str = toHexString(full_response).replace(" ", "_")
                    logger.info("Card found! UID: %s", uid_str)
                    self.uid = uid_str
                    return uid_str
            except Exception:
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
FTP_HOST = "127.0.0.1"
FTP_PORT = 2121
FTP_USER = "admin"
FTP_PASS = "1234"

MAX_FTP_RETRIES = 3
FTP_TIMEOUT = 10  # seconds

# ===================== Threading =====================
_data_lock = threading.Lock()
_operation_lock = threading.Lock()
_gui_buttons = []  # ปุ่มทั้งหมดที่ต้อง disable ขณะทำงาน

def _disable_all_buttons():
    """ปิดปุ่มทั้งหมดขณะทำงาน กันกดซ้ำ"""
    for btn in _gui_buttons:
        try:
            btn.config(state=DISABLED)
        except Exception:
            pass

def _enable_all_buttons():
    """เปิดปุ่มทั้งหมดเมื่อทำงานเสร็จ"""
    for btn in _gui_buttons:
        try:
            btn.config(state=NORMAL)
        except Exception:
            pass

# ===================== FTP HELPERS =====================
def _ftp_connect_with_retry():
    """เชื่อมต่อ FTP พร้อม retry + exponential backoff"""
    last_error = None
    for attempt in range(1, MAX_FTP_RETRIES + 1):
        try:
            ftp = FTP()
            ftp.connect(FTP_HOST, FTP_PORT, timeout=FTP_TIMEOUT)
            ftp.login(FTP_USER, FTP_PASS)
            ftp.set_pasv(True)
            logger.debug("FTP connected (attempt %d/%d)", attempt, MAX_FTP_RETRIES)
            return ftp
        except Exception as e:
            last_error = e
            delay = 2 ** (attempt - 1)  # 1, 2, 4
            logger.warning("FTP connect attempt %d/%d failed: %s — retrying in %ds",
                           attempt, MAX_FTP_RETRIES, e, delay)
            time.sleep(delay)
    raise ConnectionError(f"FTP connection failed after {MAX_FTP_RETRIES} retries: {last_error}")


def generate_and_upload_json(card_id, card_data):
    """Atomic FTP upload: STOR เป็น tmp → RNFR/RNTO rename → verify ขนาด + เนื้อหา"""
    json_bytes = json.dumps(card_data, ensure_ascii=False, indent=4).encode('utf-8')
    expected_size = len(json_bytes)
    target_file = f"{card_id}.json"
    tmp_file = f"_tmp_{card_id}.json"

    try:
        ftp = _ftp_connect_with_retry()
    except ConnectionError as e:
        logger.error("FTP upload failed (connect): %s", e)
        raise

    try:
        # สร้างโฟลเดอร์ถ้ายังไม่มี
        try:
            ftp.mkd(card_id)
        except Exception:
            pass
        ftp.cwd(card_id)

        # 1) STOR ไปที่ tmp file
        bio = BytesIO(json_bytes)
        ftp.storbinary(f'STOR {tmp_file}', bio)
        logger.debug("Uploaded tmp file: %s/%s", card_id, tmp_file)

        # 2) Atomic rename: Delete existing then rename
        try:
            ftp.delete(target_file)
        except Exception:
            # ไฟล์อาจจะยังไม่มี ไม่เป็นไร
            pass
        
        ftp.rename(tmp_file, target_file)
        logger.debug("Renamed %s → %s", tmp_file, target_file)

        # 3) Verify — ตรวจขนาดไฟล์
        server_size = ftp.size(target_file)
        if server_size is not None and server_size != expected_size:
            logger.error("FTP verify FAILED: size mismatch (expected=%d, server=%d)", expected_size, server_size)
            raise IOError(f"FTP size mismatch: expected {expected_size}, got {server_size}")

        # 4) Verify — ดาวน์โหลดกลับมาเช็ค
        verify_bio = BytesIO()
        ftp.retrbinary(f'RETR {target_file}', verify_bio.write)
        verify_bio.seek(0)
        verify_data = json.loads(verify_bio.read().decode('utf-8'))
        if verify_data != card_data:
            logger.error("FTP verify FAILED: content mismatch for %s", card_id)
            raise IOError("FTP content verification failed — data mismatch after upload")

        ftp.quit()
        logger.info("FTP upload + verify success: %s/%s (%d bytes)", card_id, target_file, expected_size)

    except Exception as e:
        logger.error("FTP upload error for %s: %s", card_id, e)
        try:
            ftp.quit()
        except Exception:
            pass
        raise


def download_card_data(card_id):
    """ดาวน์โหลดข้อมูลการ์ดจาก FTP พร้อม retry + timeout"""
    try:
        ftp = _ftp_connect_with_retry()
    except ConnectionError as e:
        logger.error("FTP download failed (connect): %s", e)
        return None

    try:
        ftp.cwd(card_id)
        bio = BytesIO()
        ftp.retrbinary(f'RETR {card_id}.json', bio.write)
        ftp.quit()
        bio.seek(0)
        data = json.loads(bio.read().decode("utf-8"))
        logger.info("FTP download success: card_id=%s, balance=%.2f", card_id, float(data.get('balance', 0)))
        return data
    except Exception as e:
        logger.error("FTP download error (card_id=%s, อาจยังไม่ได้ลงทะเบียน): %s", card_id, e)
        try:
            ftp.quit()
        except Exception:
            pass
        return None


def update_transaction_log(card_data, entry_point=None, exit_point=None, cost=None):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if exit_point is None:
        card_data["transaction_log"].append({"type": "entry", "time": timestamp, "detail": f"Entered at {entry_point}"})
        logger.info("Transaction log: ENTRY at %s, time=%s, card_id=%s", entry_point, timestamp, card_data.get("card_id"))
    else:
        card_data["transaction_log"].append({"type": "exit", "time": timestamp, "detail": f"Exited from {exit_point}, cost {cost}"})
        logger.info("Transaction log: EXIT from %s, cost=%.2f, time=%s, card_id=%s",
                     exit_point, float(cost), timestamp, card_data.get("card_id"))
    return card_data

# ===================== CORE LOGIC =====================
def calculate_cost(entry, exit):
    table = {("ด่าน A", "ด่าน B"): 150, ("ด่าน A", "ด่าน C"): 200, ("ด่าน B", "ด่าน C"): 50}
    if entry == exit: return 0
    return table.get((entry, exit)) or table.get((exit, entry), 50)

def update_signal(can_pass):
    if can_pass:
        signal_status.set("PASS")
        signal_label.config(bg="#2ECC71") # Modern Green
    else:
        signal_status.set("DENIED")
        signal_label.config(bg="#E74C3C") # Modern Red

def thread_ab():
    """ทุกครั้งที่ Tap Card จะโหลด Balance ใหม่จาก FTP เสมอ — thread-safe + ปิดปุ่ม"""
    # ใช้ _operation_lock กันกดซ้ำ (ถ้า lock ไม่ว่าง = กำลังทำงานอยู่)
    if not _operation_lock.acquire(blocking=False):
        logger.warning("Tap Card ignored: operation already in progress")
        return

    # ปิดปุ่มทั้งหมดจาก GUI thread
    root.after(0, _disable_all_buttons)

    try:
        logger.info("--- Start Syncing Data ---")
        card_id = card_id_var.get()

        # ดึงข้อมูลใหม่สดๆ จาก FTP
        card_data = download_card_data(card_id)
        if card_data is None:
            root.after(0, lambda: update_signal(False))
            root.after(0, lambda: balance_var.set("0.00"))
            logger.warning("DENIED: card_id=%s — ไม่พบข้อมูลบน FTP (ยังไม่ได้ลงทะเบียน)", card_id)
            return

        # อัปเดตยอดเงินล่าสุดบน GUI (thread-safe ผ่าน root.after)
        with _data_lock:
            balance = float(card_data.get("balance", 0))
        root.after(0, lambda b=balance: balance_var.set(f"{b:.2f}"))

        entry_point = entry_var.get()
        exit_point = exit_var.get()
        can_pass = False

        if mode_var.get() == "entry":
            if balance >= 200:
                can_pass = True
            else:
                logger.warning("DENIED: card_id=%s, mode=entry, balance=%.2f < 200 (เงินไม่พอ)", card_id, balance)
        else:
            cost = calculate_cost(entry_point, exit_point)
            root.after(0, lambda c=cost: cost_var.set(str(c)))
            if balance >= cost:
                can_pass = True
            else:
                logger.warning("DENIED: card_id=%s, mode=exit, balance=%.2f < cost=%d, entry=%s, exit=%s",
                               card_id, balance, cost, entry_point, exit_point)

        root.after(0, lambda cp=can_pass: update_signal(cp))

        if can_pass:
            logger.info("PASS: card_id=%s, balance=%.2f, mode=%s, entry=%s, exit=%s",
                        card_id, balance, mode_var.get(), entry_point, exit_point)
            # ทำ thread_cd ต่อใน thread เดียวกัน (ไม่ต้องสร้าง thread ใหม่ เพื่อให้ lock ทำงานถูกต้อง)
            thread_cd(card_data)
        else:
            logger.info("Signal: DENIED for card_id=%s", card_id)

    except Exception as e:
        logger.error("Unexpected error in thread_ab: %s", e)
    finally:
        # เปิดปุ่มกลับ + ปล่อย lock
        root.after(0, _enable_all_buttons)
        _operation_lock.release()


def thread_cd(card_data):
    """อัปเดต transaction log + อัปโหลดกลับไป FTP (thread-safe)"""
    with _data_lock:
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
            new_balance = card_data["balance"]
            root.after(0, lambda nb=new_balance: balance_var.set(f"{nb:.2f}"))

    try:
        generate_and_upload_json(card_id, card_data)
        logger.info("FTP update success for card_id=%s", card_id)
    except Exception as e:
        logger.error("FTP update FAILED for card_id=%s: %s", card_id, e)


def reset_fields():
    """อ่านเลขบัตรใหม่ + โหลดข้อมูลใหม่ — ปิดปุ่มขณะทำงาน"""
    _disable_all_buttons()
    try:
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
        logger.info("Reset fields. New card_id=%s", card_id)
    except Exception as e:
        logger.error("Reset failed: %s", e)
    finally:
        _enable_all_buttons()

# ===================== GUI SETUP =====================
root = Tk()
root.geometry("900x550")
root.title("NFC Toll System")

bg_color = "#F4F7FB"
card_bg = "#FFFFFF"
primary_color = "#4361EE"
success_color = "#2ECC71"
warning_color = "#F39C12"
text_color = "#2B2D42"

root.configure(bg=bg_color)

card_id_var = StringVar(); entry_var = StringVar(value="ด่าน A")
exit_var = StringVar(value="ด่าน B"); balance_var = StringVar()
cost_var = StringVar(value="0"); signal_status = StringVar(value="READY")
mode_var = StringVar(value="entry")

# Title
Label(root, text="🛣️ NFC Toll Gate System", font=('TH Sarabun New', 32, 'bold'),
      bg=bg_color, fg=primary_color).pack(pady=(20, 10))

# UI Frame
f1 = Frame(root, bg=card_bg, padx=30, pady=30, highlightbackground="#E2E8F0", highlightthickness=1)
f1.pack(pady=10, padx=40, fill="both", expand=True)

label_font = ('TH Sarabun New', 18, 'bold')
val_font = ('TH Sarabun New', 18)

Label(f1, text="Card ID:", font=label_font, bg=card_bg, fg=text_color).grid(row=0, column=0, sticky='e', padx=10, pady=15)
Label(f1, textvariable=card_id_var, font=val_font, bg="#F8FAFC", fg=primary_color, width=22, relief="solid", borderwidth=1).grid(row=0, column=1, padx=10, pady=15)

Label(f1, text="Balance (THB):", font=label_font, bg=card_bg, fg=text_color).grid(row=0, column=2, sticky='e', padx=10, pady=15)
Label(f1, textvariable=balance_var, font=val_font, bg="#E8F4F8", fg="#2980B9", width=18, relief="solid", borderwidth=1).grid(row=0, column=3, padx=10, pady=15)

Label(f1, text="Entry Station:", font=label_font, bg=card_bg, fg=text_color).grid(row=1, column=0, sticky='e', padx=10, pady=15)
op_entry = OptionMenu(f1, entry_var, "ด่าน A", "ด่าน B", "ด่าน C")
op_entry.config(font=val_font, bg="#F8FAFC", width=15, relief="solid", borderwidth=1)
op_entry.grid(row=1, column=1, padx=10, pady=15, sticky='w')

Label(f1, text="Exit Station:", font=label_font, bg=card_bg, fg=text_color).grid(row=2, column=0, sticky='e', padx=10, pady=15)
op_exit = OptionMenu(f1, exit_var, "ด่าน A", "ด่าน B", "ด่าน C")
op_exit.config(font=val_font, bg="#F8FAFC", width=15, relief="solid", borderwidth=1)
op_exit.grid(row=2, column=1, padx=10, pady=15, sticky='w')

Label(f1, text="Cost (THB):", font=label_font, bg=card_bg, fg=text_color).grid(row=1, column=2, sticky='e', padx=10, pady=15)
Label(f1, textvariable=cost_var, font=val_font, bg="#FDEBD0", fg="#D35400", width=18, relief="solid", borderwidth=1).grid(row=1, column=3, padx=10, pady=15)

Label(f1, text="Gate Signal:", font=label_font, bg=card_bg, fg=text_color).grid(row=2, column=2, sticky='e', padx=10, pady=15)
signal_label = Label(f1, textvariable=signal_status, font=('TH Sarabun New', 18, 'bold'), width=18, fg="white", bg="#7F8C8D", relief="solid", borderwidth=1)
signal_label.grid(row=2, column=3, padx=10, pady=15)

frame_mode = Frame(f1, bg=card_bg)
frame_mode.grid(row=3, column=0, columnspan=2, pady=15)

Radiobutton(frame_mode, text="Entry Mode", variable=mode_var, value="entry", font=('TH Sarabun New', 16, 'bold'), bg=card_bg, fg=text_color, selectcolor=card_bg).pack(side=LEFT, padx=10)
Radiobutton(frame_mode, text="Exit Mode", variable=mode_var, value="exit", font=('TH Sarabun New', 16, 'bold'), bg=card_bg, fg=text_color, selectcolor=card_bg).pack(side=LEFT, padx=10)

frame_btns = Frame(f1, bg=card_bg)
frame_btns.grid(row=4, column=0, columnspan=4, pady=25)

btn_tap = Button(frame_btns, text="📡 Tap Card (Sync & Pay)", font=('TH Sarabun New', 18, 'bold'), bg=success_color, fg="white", relief="flat", activebackground="#27AE60", activeforeground="white", width=25,
       command=lambda: threading.Thread(target=thread_ab, daemon=True).start())
btn_tap.pack(side=LEFT, padx=15)

btn_reset = Button(frame_btns, text="🔄 Reset Reader", font=('TH Sarabun New', 16, 'bold'), bg="#95A5A6", fg="white", relief="flat", activebackground="#7F8C8D", activeforeground="white", width=15,
       command=lambda: threading.Thread(target=reset_fields, daemon=True).start())
btn_reset.pack(side=LEFT, padx=15)

# เก็บ reference ปุ่มทั้งหมดเพื่อ disable/enable
_gui_buttons.extend([btn_tap, btn_reset])

# Start NFC Reader
reader = NFC_Reader()
card_id = reader.read_uid()
card_id_var.set(card_id)

# โหลดข้อมูลครั้งแรก
initial_data = download_card_data(card_id)
if initial_data:
    balance_var.set(f"{initial_data.get('balance'):.2f}")

logger.info("NFC Toll System started. card_id=%s", card_id)

root.mainloop()
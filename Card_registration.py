from tkinter import *
from tkinter import messagebox
import random
import ssl
import smtplib
import json
import time
import os
import logging
import threading
from ftplib import FTP
from email.message import EmailMessage
from io import BytesIO
import datetime
from smartcard.scard import *
from smartcard.util import toHexString

# ===================== LOGGING SETUP =====================
logger = logging.getLogger("NFC_Registration")
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
            except Exception as e:
                logger.debug("Reconnect failed: %s", e)

            time.sleep(1)

    def write_data(self, string):
        int_array = list(map(ord, string))
        logger.debug("Writing data: %s", int_array)
        if len(int_array) > 16:
            return
        command = UPDATE_FIXED_BLOCKS + int_array + [0x00] * (16 - len(int_array))
        response, _ = self.send_command(AUTHENTICATE)
        if response == [144, 0]:
            logger.info("Authentication successful. Writing data...")
            self.send_command(command)
        else:
            logger.warning("Unable to authenticate for write.")

    def read_data(self):
        response, _ = self.send_command(AUTHENTICATE)
        if response == [144, 0]:
            _, value = self.send_command(READ_16_BINARY_BLOCKS)
            return value
        else:
            logger.warning("Unable to authenticate for read.")
            return None

# ===================== Data =====================
card_data = {}
accounts_data = {}

# ===================== Threading =====================
_ftp_lock = threading.Lock()
_gui_buttons = []  # จะเก็บ reference ปุ่มทั้งหมดเพื่อ disable/enable

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

# ===================== Email =====================
def generate_otp(length=6):
    return ''.join(str(random.randint(0, 9)) for _ in range(length))

def send_otp_by_email(receiver_email, otp):
    """ส่ง OTP ทาง email — return True ถ้าสำเร็จ, False ถ้าล้มเหลว"""
    smtp_server = "smtp.gmail.com"
    port = 587
    sender_email = "nice456789123@gmail.com"
    password = "gfeq hnxn odxy xwbd"

    message = EmailMessage()
    message.set_content(f"Your OTP is: {otp}")
    message["Subject"] = "Your OTP Code"
    message["From"] = sender_email
    message["To"] = receiver_email

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(smtp_server, port, timeout=15) as server:
            server.starttls(context=context)
            server.login(sender_email, password)
            server.send_message(message)
        logger.info("OTP sent successfully to %s", receiver_email)
        return True
    except Exception as e:
        logger.error("Error sending OTP email to %s: %s", receiver_email, e)
        return False

# ===================== FTP HELPERS =====================
FTP_HOST = "localhost"
FTP_PORT = 2121
FTP_USER = "admin"
FTP_PASS = "1234"

MAX_FTP_RETRIES = 3
FTP_TIMEOUT = 10  # seconds

def _ftp_connect_with_retry():
    """เชื่อมต่อ FTP พร้อม retry + exponential backoff — return FTP object หรือ raise exception"""
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
            logger.warning("FTP connect attempt %d/%d failed: %s — retrying in %ds", attempt, MAX_FTP_RETRIES, e, delay)
            time.sleep(delay)
    raise ConnectionError(f"FTP connection failed after {MAX_FTP_RETRIES} retries: {last_error}")


def download_json_from_ftp(card_id):
    """ดาวน์โหลด JSON จาก FTP พร้อม retry + timeout"""
    target_file = f"{card_id}.json"
    try:
        ftp = _ftp_connect_with_retry()
    except ConnectionError as e:
        logger.error("FTP download failed (connect): %s", e)
        return None

    try:
        try:
            ftp.cwd(card_id)
        except Exception:
            ftp.quit()
            return None

        files = ftp.nlst()
        if target_file not in files:
            ftp.quit()
            return None

        bio = BytesIO()
        ftp.retrbinary('RETR ' + target_file, bio.write)
        ftp.quit()
        bio.seek(0)
        data = json.loads(bio.read().decode('utf-8'))
        logger.info("FTP download success: %s/%s", card_id, target_file)
        return data
    except Exception as e:
        logger.error("FTP download error for %s: %s", card_id, e)
        try:
            ftp.quit()
        except Exception:
            pass
        return None


def generate_and_upload_json(card_id, card_data_to_upload):
    """Atomic FTP upload: STOR เป็น tmp → RNFR/RNTO rename → verify ขนาด + เนื้อหา"""
    json_bytes = json.dumps(card_data_to_upload, ensure_ascii=False, indent=4).encode('utf-8')
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
        if verify_data != card_data_to_upload:
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


# ===================== OTP CONFIG =====================
OTP_EXPIRY_SECONDS = 120    # OTP หมดอายุ 2 นาที
OTP_MAX_ATTEMPTS = 5        # จำกัด verify ผิดสูงสุด 5 ครั้ง
OTP_MAX_SEND = 5            # จำกัดส่ง OTP สูงสุด 5 ครั้งต่อ card_id

# ===================== ฟังก์ชันสำหรับ GUI การลงทะเบียน =====================
def send_otp():
    """
    1. ดึง Card ID กับ Email จากช่องกรอก
    2. ตรวจสอบว่ามี Card ID นี้ใน FTP Server อยู่หรือไม่
    3. ตรวจจำนวนครั้งที่ส่ง OTP
    4. ส่ง OTP — ถ้า email fail → หยุดทันที ไม่บันทึก OTP
    """
    _disable_all_buttons()
    try:
        card_id = card_id_var.get().strip()
        email = email_var.get().strip()

        if not card_id:
            status_var.set("กรุณาใส่ Card ID")
            logger.warning("Send OTP: card_id is empty")
            return
        if not email:
            status_var.set("กรุณาใส่ Email")
            logger.warning("Send OTP: email is empty")
            return

        # ตรวจจำนวนครั้งที่ส่ง OTP
        if card_id in card_data and card_data[card_id].get("send_count", 0) >= OTP_MAX_SEND:
            status_var.set("ส่ง OTP เกินจำนวนครั้งที่กำหนด กรุณารอสักครู่")
            logger.warning("Send OTP DENIED: card_id=%s exceeded max send count (%d)", card_id, OTP_MAX_SEND)
            messagebox.showerror("Error", f"ส่ง OTP เกิน {OTP_MAX_SEND} ครั้งแล้ว กรุณารอสักครู่")
            return

        # ตรวจสอบว่ามี Card ID นี้ใน FTP Server อยู่หรือไม่
        ftp_data = download_json_from_ftp(card_id)
        if ftp_data is not None:
            status_var.set("Card ID มีอยู่แล้วในระบบ FTP")
            logger.warning("Send OTP DENIED: card_id=%s already exists on FTP", card_id)
            messagebox.showerror("Error", f"Card ID {card_id} มีอยู่แล้วในระบบ FTP ไม่สามารถส่ง OTP ได้")
            return

        new_otp = generate_otp()

        # ส่ง email ก่อน — ถ้า fail จะไม่บันทึก OTP
        success = send_otp_by_email(email, new_otp)
        if not success:
            status_var.set("ส่ง OTP ล้มเหลว กรุณาลองใหม่")
            logger.error("Send OTP FAILED: email send failed for card_id=%s, email=%s", card_id, email)
            messagebox.showerror("Error", "ไม่สามารถส่ง OTP ได้ กรุณาตรวจสอบอีเมลและลองใหม่")
            return

        # ส่งสำเร็จ → บันทึก OTP + timestamp + counters
        if card_id not in card_data:
            card_data[card_id] = {
                "email": email, "otp": new_otp, "registered": False,
                "otp_time": time.time(), "verify_attempts": 0, "send_count": 1
            }
        else:
            card_data[card_id]["email"] = email
            card_data[card_id]["otp"] = new_otp
            card_data[card_id]["registered"] = False
            card_data[card_id]["otp_time"] = time.time()
            card_data[card_id]["verify_attempts"] = 0
            card_data[card_id]["send_count"] = card_data[card_id].get("send_count", 0) + 1

        # ไม่โชว์ OTP บนหน้าจอ
        status_var.set(f"OTP ถูกส่งไปยัง {email} แล้ว")
        logger.info("OTP sent: card_id=%s, email=%s", card_id, email)
        messagebox.showinfo("OTP Sent", f"OTP ถูกส่งไปยัง {email} เรียบร้อยแล้ว!")
    finally:
        _enable_all_buttons()


def confirm_otp():
    """
    1. ตรวจสอบ OTP ที่กรอกกับข้อมูลใน card_data
    2. ตรวจหมดอายุ (2 นาที) + จำนวนครั้ง verify
    3. ถ้า OTP ถูกต้อง → ลงทะเบียน + อัปโหลด FTP
    """
    _disable_all_buttons()
    try:
        card_id = card_id_var.get().strip()
        input_otp = otp_var.get().strip()

        if card_id not in card_data:
            status_var.set("ไม่พบข้อมูล Card ID กรุณาส่ง OTP ก่อน")
            logger.warning("Confirm OTP DENIED: card_id=%s not found in card_data", card_id)
            return

        record = card_data[card_id]

        # ตรวจจำนวนครั้ง verify
        if record.get("verify_attempts", 0) >= OTP_MAX_ATTEMPTS:
            status_var.set("ยืนยัน OTP เกินจำนวนครั้งที่กำหนด กรุณาส่ง OTP ใหม่")
            logger.warning("Confirm OTP DENIED: card_id=%s exceeded max verify attempts (%d)", card_id, OTP_MAX_ATTEMPTS)
            messagebox.showerror("Error", f"ยืนยัน OTP เกิน {OTP_MAX_ATTEMPTS} ครั้งแล้ว กรุณาส่ง OTP ใหม่")
            return

        # ตรวจหมดอายุ
        otp_age = time.time() - record.get("otp_time", 0)
        if otp_age > OTP_EXPIRY_SECONDS:
            status_var.set("OTP หมดอายุแล้ว กรุณาส่ง OTP ใหม่")
            logger.warning("Confirm OTP DENIED: card_id=%s OTP expired (%.0fs > %ds)", card_id, otp_age, OTP_EXPIRY_SECONDS)
            messagebox.showerror("Error", "OTP หมดอายุแล้ว กรุณาส่ง OTP ใหม่")
            return

        record["verify_attempts"] = record.get("verify_attempts", 0) + 1

        correct_otp = record["otp"]
        if input_otp == correct_otp:
            # ตรวจสอบใน FTP Server ว่ามีข้อมูล card_id อยู่หรือไม่
            ftp_data = download_json_from_ftp(card_id)
            if ftp_data is not None:
                status_var.set("Card นี้ลงทะเบียนไปแล้ว ไม่สามารถลงทะเบียนซ้ำได้")
                logger.warning("Confirm OTP DENIED: card_id=%s already registered on FTP", card_id)
                messagebox.showerror("Error", "Card นี้ลงทะเบียนไปแล้ว กรุณาใช้ Card อื่น หรือเข้าสู่ระบบเติมเงิน")
                return

            record["registered"] = True
            sample_data = {
                "card_id": card_id,
                "balance": 0,
                "email": record["email"],
                "top_up_history": [],
                "transaction_log": []
            }
            try:
                generate_and_upload_json(card_id, sample_data)
                accounts_data[card_id] = sample_data
                status_var.set(f"Card {card_id} ลงทะเบียนสำเร็จ!")
                logger.info("Registration SUCCESS: card_id=%s, email=%s", card_id, record["email"])
                messagebox.showinfo("Success", f"Card {card_id} ลงทะเบียนและอัปโหลดข้อมูลสำเร็จ!")
            except Exception as e:
                status_var.set("อัปโหลดข้อมูลล้มเหลว กรุณาลองใหม่")
                logger.error("Registration FAILED: card_id=%s, FTP upload error: %s", card_id, e)
                messagebox.showerror("Error", f"อัปโหลดข้อมูลล้มเหลว: {e}")
        else:
            status_var.set("OTP ไม่ถูกต้อง")
            logger.warning("Confirm OTP DENIED: card_id=%s wrong OTP (attempt %d/%d)",
                           card_id, record["verify_attempts"], OTP_MAX_ATTEMPTS)
    finally:
        _enable_all_buttons()


# ===================== ฟังก์ชันสำหรับหน้าการเติมเงิน =====================
def open_top_up_window():
    top_window = Toplevel(root)
    top_window.geometry("400x300")
    top_window.title("Top Up")
    top_window.configure(bg="seashell2")

    top_card_id_var.set(card_id)

    Label(top_window, text="Top Up", font=('TH Saraban New', 20, 'bold'),
          bg="seashell2", fg="blue").pack(pady=10)

    frame_top = Frame(top_window, bg="seashell2")
    frame_top.pack(pady=10)

    Label(frame_top, text="Card ID:", font=('TH Sarabun New', 16, 'bold'),
          bg="seashell2").grid(row=0, column=0, padx=5, pady=5, sticky='e')
    Label(frame_top, textvariable=top_card_id_var, font=('TH Sarabun New', 16),
          width=20, bg="white").grid(row=0, column=1, padx=5, pady=5)

    Label(frame_top, text="Amount:", font=('TH Sarabun New', 16, 'bold'),
          bg="seashell2").grid(row=1, column=0, padx=5, pady=5, sticky='e')
    Entry(frame_top, textvariable=top_amount_var, font=('TH Sarabun New', 16),
          width=20, bg="white").grid(row=1, column=1, padx=5, pady=5)

    def perform_top_up():
        btn_topup.config(state=DISABLED)
        try:
            c_id = top_card_id_var.get().strip()
            amount_str = top_amount_var.get().strip()
            if c_id == "":
                top_status_var.set("กรุณาใส่ Card ID")
                return
            if amount_str == "":
                top_status_var.set("กรุณาใส่จำนวนเงิน")
                return
            try:
                amount = float(amount_str)
            except ValueError:
                top_status_var.set("จำนวนเงินไม่ถูกต้อง")
                logger.warning("Top-up DENIED: invalid amount '%s' for card_id=%s", amount_str, c_id)
                return

            # ตรวจสอบข้อมูลใน FTP Server ว่ามี card_id หรือไม่
            ftp_data = download_json_from_ftp(c_id)
            if ftp_data is None:
                top_status_var.set("Card ID ไม่พบในระบบ FTP กรุณาลงทะเบียนก่อน")
                logger.warning("Top-up DENIED: card_id=%s not found on FTP", c_id)
                return

            # ใช้ข้อมูลจาก FTP มาอัปเดตบัญชี
            accounts_data[c_id] = ftp_data
            account = accounts_data[c_id]
            account["balance"] += amount

            # บันทึกเวลาเติมเงิน
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            account["top_up_history"].append({"amount": amount, "time": timestamp})

            # อัปเดตไฟล์ JSON บน FTP ด้วยข้อมูลที่อัปเดตแล้ว
            try:
                generate_and_upload_json(c_id, account)
                top_status_var.set(f"เติมเงินสำเร็จ! ยอดเงินใหม่: {account['balance']}")
                logger.info("Top-up SUCCESS: card_id=%s, amount=%.2f, new_balance=%.2f, time=%s",
                            c_id, amount, account['balance'], timestamp)
                messagebox.showinfo("Success", f"เติมเงินสำเร็จ!\nยอดเงินใหม่: {account['balance']}\nเวลา: {timestamp}")
            except Exception as e:
                top_status_var.set("อัปโหลดข้อมูลล้มเหลว กรุณาลองใหม่")
                logger.error("Top-up FAILED: card_id=%s, FTP upload error: %s", c_id, e)
                messagebox.showerror("Error", f"เติมเงินล้มเหลว: {e}")
        finally:
            btn_topup.config(state=NORMAL)

    btn_topup = Button(frame_top, text="Top Up", font=('TH Sarabun New', 14, 'bold'),
                       command=perform_top_up, bg="light green")
    btn_topup.grid(row=2, column=0, columnspan=2, pady=10)

    Label(top_window, textvariable=top_status_var, font=('TH Sarabun New', 16, 'bold'),
          fg="red", bg="seashell2").pack(pady=10)

    Button(top_window, text="Close", font=('TH Sarabun New', 14, 'bold'),
           command=top_window.destroy, bg="tomato").pack(pady=10)

# ===================== ฟังก์ชันสำหรับ Reset และ Exit =====================
def reset_fields():
    global card_id
    _disable_all_buttons()
    try:
        reader = NFC_Reader()
        card_id_new = reader.read_uid().replace(" ", "_")
        card_id = card_id_new  # อัปเดต global card_id
        
        card_id_var.set(card_id_new)
        top_card_id_var.set(card_id_new)
        
        # ล้างค่าในหน้า Top Up ด้วย
        top_amount_var.set("")
        top_status_var.set("")
        
        email_var.set("")
        otp_var.set("")
        status_var.set("")
        
        logger.info("Fields reset. New card_id=%s", card_id_new)
    except Exception as e:
        logger.error("Reset failed: %s", e)
        status_var.set("Reset ล้มเหลว")
    finally:
        _enable_all_buttons()

def exit_app():
    logger.info("Application exiting.")
    root.destroy()

# ===================== ส่วน GUI หลัก =====================
root = Tk()
root.geometry("600x400")
root.title("NFC_registration")
root.configure(bg="seashell2")

card_id_var = StringVar()
email_var = StringVar()
otp_var = StringVar()
status_var = StringVar()

top_card_id_var = StringVar()
top_amount_var = StringVar()
top_status_var = StringVar()

# Title
Label(root, text="NFC Card Registration", font=('TH Saraban New', 24, 'bold'),
      bg="seashell2", fg="blue").pack(pady=10)

# Frame สำหรับการลงทะเบียน
frame_main = Frame(root, bg="seashell2")
frame_main.pack(pady=10)

Label(frame_main, text="Card ID:", font=('TH Sarabun New', 16, 'bold'),
      bg="seashell2").grid(row=0, column=0, padx=5, pady=5, sticky='e')
Label(frame_main, textvariable=card_id_var, font=('TH Sarabun New', 16),
      width=20, bg="white").grid(row=0, column=1, padx=5, pady=5)

Label(frame_main, text="Email:", font=('TH Sarabun New', 16, 'bold'),
      bg="seashell2").grid(row=1, column=0, padx=5, pady=5, sticky='e')
Entry(frame_main, textvariable=email_var, font=('TH Sarabun New', 16),
      width=20, bg="white").grid(row=1, column=1, padx=5, pady=5)

btn_send_otp = Button(frame_main, text="Send OTP", font=('TH Sarabun New', 14, 'bold'),
                      command=send_otp, bg="light green")
btn_send_otp.grid(row=1, column=2, padx=5, pady=5)

Label(frame_main, text="OTP:", font=('TH Sarabun New', 16, 'bold'),
      bg="seashell2").grid(row=2, column=0, padx=5, pady=5, sticky='e')
Entry(frame_main, textvariable=otp_var, font=('TH Sarabun New', 16),
      width=20, bg="white").grid(row=2, column=1, padx=5, pady=5)

btn_confirm = Button(frame_main, text="Confirm", font=('TH Sarabun New', 14, 'bold'),
                     command=confirm_otp, bg="light blue")
btn_confirm.grid(row=2, column=2, padx=5, pady=5)

# ปุ่มสำหรับ Reset, Top Up และ Exit
btn_reset = Button(frame_main, text="Reset", font=('TH Sarabun New', 14, 'bold'),
                   command=reset_fields, bg="orange")
btn_reset.grid(row=3, column=1, padx=5, pady=20, sticky='e')

btn_exit = Button(frame_main, text="Exit", font=('TH Sarabun New', 14, 'bold'),
                  command=exit_app, bg="tomato")
btn_exit.grid(row=3, column=2, padx=5, pady=20, sticky='w')

# ปุ่มเปิดหน้าการเติมเงิน
btn_topup_main = Button(root, text="Top Up", font=('TH Sarabun New', 14, 'bold'),
                        command=open_top_up_window, bg="light blue")
btn_topup_main.pack(pady=5)

# เก็บ reference ปุ่มทั้งหมดเพื่อ disable/enable
_gui_buttons.extend([btn_send_otp, btn_confirm, btn_reset, btn_exit, btn_topup_main])

Label(root, textvariable=status_var, font=('TH Sarabun New', 16, 'bold'),
      fg="red", bg="seashell2").pack(pady=10)

reader = NFC_Reader()

card_id = reader.read_uid()

card_id_var.set(card_id)

logger.info("Application started. card_id=%s", card_id)

root.mainloop()
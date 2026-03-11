from tkinter import *
from tkinter import messagebox
import random
import ssl
import smtplib
import json
import time
import os
from ftplib import FTP  # เปลี่ยนจาก FTP_TLS เป็น FTP ธรรมดา
from email.message import EmailMessage
from io import BytesIO
import datetime
from smartcard.scard import *
from smartcard.util import toHexString

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
                if VERBOSE:
                    print("Value: " + value + " , Response:  " + str(self.response) + " HResult: " + str(self.hresult))
            except Exception as e:
                print("No Card Found")
                print("Eror:", e)
                return None, None
            time.sleep(1)
        print("------------------------\n")
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
                print("Error:", e)
                print("Card Not Found")
    
            try:
                self.hresult, self.hcard, self.dwActiveProtocol = SCardConnect(
                    self.hcontext,
                    self.reader,
                    SCARD_SHARE_SHARED,
                    SCARD_PROTOCOL_T0 | SCARD_PROTOCOL_T1)
            except Exception as e:
                print("Reconnect failed:", e)
    
            time.sleep(1)
            

    def write_data(self, string):
        int_array = list(map(ord, string))
        print("Writing data: " + str(int_array))
        if len(int_array) > 16:
            return
        command = UPDATE_FIXED_BLOCKS + int_array + [0x00] * (16 - len(int_array))
        response, _ = self.send_command(AUTHENTICATE)
        if response == [144, 0]:
            print("Authentication successful. Writing data...")
            self.send_command(command)
        else:
            print("Unable to authenticate.")

    def read_data(self):
        response, _ = self.send_command(AUTHENTICATE)
        if response == [144, 0]:
            _, value = self.send_command(READ_16_BINARY_BLOCKS)
            return value
        else:
            print("Unable to authenticate.")
            return None

# ===================== Data =====================
card_data = {}
accounts_data = {}

# ===================== Email =====================
def generate_otp(length=6):
    return ''.join(str(random.randint(0, 9)) for _ in range(length))

def send_otp_by_email(receiver_email, otp):
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
        with smtplib.SMTP(smtp_server, port) as server:
            server.starttls(context=context)
            server.login(sender_email, password)
            server.send_message(message)
        print("OTP sent successfully!")
    except Exception as e:
        print("Error sending email:", e)

# ===================== FTP (FIXED FOR NO TLS) =====================
def download_json_from_ftp(card_id):
    ftp_host = "127.0.0.1"
    ftp_port = 2121
    ftp_user = "admin"
    ftp_pass = "1234"
    target_file = f"{card_id}.json"

    ftp = None
    try:
        ftp = FTP()
        ftp.connect(ftp_host, ftp_port, timeout=5)
        ftp.login(ftp_user, ftp_pass)

        buffer = BytesIO()
        ftp.retrbinary(f"RETR {target_file}", buffer.write)
        ftp.quit()

        buffer.seek(0)
        return json.loads(buffer.read().decode('utf-8'))
    except Exception as e:
        if ftp:
            try: ftp.quit()
            except: pass
        print(f"FTP Info: {target_file} not found or {e}")
        return None

def generate_and_upload_json(card_id, card_data):
    ftp_host = "127.0.0.1"
    ftp_port = 2121
    ftp_user = "admin"
    ftp_pass = "1234"
    filename = f"{card_id}.json"

    ftp = None
    try:
        json_content = json.dumps(card_data, indent=4, ensure_ascii=False)
        buffer = BytesIO(json_content.encode('utf-8'))

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


# ===================== ฟังก์ชันสำหรับ GUI การลงทะเบียน =====================
def send_otp():
    """
    1. ดึง Card ID กับ Email จากช่องกรอก
    2. ตรวจสอบว่ามี Card ID นี้ใน FTP Server อยู่หรือไม่
       - ถ้ามี: แสดงข้อความแจ้งว่า Card ID มีอยู่แล้วและไม่ส่ง OTP
       - ถ้าไม่: สร้าง OTP ส่งอีเมลและแสดง popup แจ้งว่า OTP ถูกส่งเรียบร้อยแล้ว
    """
    card_id = card_id_var.get().strip()
    email = email_var.get().strip()

    if not card_id:
        status_var.set("กรุณาใส่ Card ID")
        return
    if not email:
        status_var.set("กรุณาใส่ Email")
        return

    # ตรวจสอบว่ามี Card ID นี้ใน FTP Server อยู่หรือไม่
    ftp_data = download_json_from_ftp(card_id)
    if ftp_data is not None:
        status_var.set("Card ID มีอยู่แล้วในระบบ FTP")
        messagebox.showerror("Error", f"Card ID {card_id} มีอยู่แล้วในระบบ FTP ไม่สามารถส่ง OTP ได้")
        return

    new_otp = generate_otp()
    if card_id not in card_data:
        card_data[card_id] = {"email": email, "otp": new_otp, "registered": False}
    else:
        card_data[card_id]["email"] = email
        card_data[card_id]["otp"] = new_otp
        card_data[card_id]["registered"] = False

    send_otp_by_email(email, new_otp)
    status_var.set(f"OTP sent to {email} (ตัวอย่าง OTP: {new_otp})")
    messagebox.showinfo("OTP Sent", f"OTP ถูกส่งไปยัง {email} เรียบร้อยแล้ว!")

def confirm_otp():
    """
    1. ตรวจสอบ OTP ที่กรอกกับข้อมูลใน card_data
    2. ถ้า OTP ถูกต้อง: ตรวจสอบว่าลงทะเบียนไปแล้วหรือยังโดยดูจาก FTP Server
       - ถ้าลงทะเบียนไปแล้ว: แจ้งเตือนไม่ให้ลงทะเบียนซ้ำ
       - ถ้ายังไม่ลงทะเบียน: สร้างข้อมูลบัญชีและอัปโหลดไปยัง FTP
    3. แสดงข้อความแจ้งทำงานสำเร็จ
    """
    card_id = card_id_var.get().strip()
    input_otp = otp_var.get().strip()
  
    if card_id not in card_data:
        status_var.set("ไม่พบข้อมูล Card ID กรุณาส่ง OTP ก่อน")
        return

    correct_otp = card_data[card_id]["otp"]
    if input_otp == correct_otp:
        # ตรวจสอบใน FTP Server ว่ามีข้อมูล card_id อยู่หรือไม่
        ftp_data = download_json_from_ftp(card_id)
        if ftp_data is not None:
            status_var.set("Card นี้ลงทะเบียนไปแล้ว ไม่สามารถลงทะเบียนซ้ำได้")
            messagebox.showerror("Error", "Card นี้ลงทะเบียนไปแล้ว กรุณาใช้ Card อื่น หรือเข้าสู่ระบบเติมเงิน")
            return

        card_data[card_id]["registered"] = True
        sample_data = {
            "card_id": card_id,
            "balance": 0,
            "email": card_data[card_id]["email"],
            "top_up_history": [],
            "transaction_log": []
        }
        try:
            generate_and_upload_json(card_id, sample_data)
            accounts_data[card_id] = sample_data
        except Exception as E:
            print("Error:", E)
        status_var.set(f"Card {card_id} ลงทะเบียนสำเร็จ!")
        messagebox.showinfo("Success", f"Card {card_id} ลงทะเบียนและอัปโหลดข้อมูลสำเร็จ! ทำงานสำเร็จ")
    else:
        status_var.set("OTP ไม่ถูกต้อง")

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
        card_id = top_card_id_var.get().strip()
        amount_str = top_amount_var.get().strip()
        if card_id == "":
            top_status_var.set("กรุณาใส่ Card ID")
            return
        if amount_str == "":
            top_status_var.set("กรุณาใส่จำนวนเงิน")
            return
        try:
            amount = float(amount_str)
        except ValueError:
            top_status_var.set("จำนวนเงินไม่ถูกต้อง")
            return
        
        # ตรวจสอบข้อมูลใน FTP Server ว่ามี card_id หรือไม่
        ftp_data = download_json_from_ftp(card_id)
        if ftp_data is None:
            top_status_var.set("Card ID ไม่พบในระบบ FTP กรุณาลงทะเบียนก่อน")
            return
        
        # ใช้ข้อมูลจาก FTP มาอัปเดตบัญชี
        accounts_data[card_id] = ftp_data
        account = accounts_data[card_id]
        account["balance"] += amount
        
        # บันทึกเวลาเติมเงิน
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        account["top_up_history"].append({"amount": amount, "time": timestamp})
        
        # อัปเดตไฟล์ JSON บน FTP ด้วยข้อมูลที่อัปเดตแล้ว
        generate_and_upload_json(card_id, account)
        
        top_status_var.set(f"เติมเงินสำเร็จ! ยอดเงินใหม่: {account['balance']}")
        messagebox.showinfo("Success", f"เติมเงินสำเร็จ!\nยอดเงินใหม่: {account['balance']}\nเวลา: {timestamp}\nทำงานสำเร็จ")
    
    Button(frame_top, text="Top Up", font=('TH Sarabun New', 14, 'bold'),
           command=perform_top_up, bg="light green").grid(row=2, column=0, columnspan=2, pady=10)
    
    Label(top_window, textvariable=top_status_var, font=('TH Sarabun New', 16, 'bold'),
          fg="red", bg="seashell2").pack(pady=10)
    
    Button(top_window, text="Close", font=('TH Sarabun New', 14, 'bold'),
           command=top_window.destroy, bg="tomato").pack(pady=10)

# ===================== ฟังก์ชันสำหรับ Reset และ Exit =====================
def reset_fields():
    reader = NFC_Reader()
    card_id = reader.read_uid().replace(" ", "_")
    
    card_id_var.set(card_id)
    top_card_id_var.set(card_id)
    
    email_var.set("")
    otp_var.set("")
    status_var.set("")

def exit_app():
    root.destroy()

# ===================== ส่วน GUI หลัก =====================
root = Tk()
root.geometry("600x400")
root.title("NFC_registration")
root.configure(bg="seashell2")

card_id_var = StringVar()
email_var = StringVar()
otp_var = StringVar()
status_var = StringVar()  # สำหรับแสดงสถานะ/ข้อความ

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

Button(frame_main, text="Send OTP", font=('TH Sarabun New', 14, 'bold'),
       command=send_otp, bg="light green").grid(row=1, column=2, padx=5, pady=5)

Label(frame_main, text="OTP:", font=('TH Sarabun New', 16, 'bold'),
      bg="seashell2").grid(row=2, column=0, padx=5, pady=5, sticky='e')
Entry(frame_main, textvariable=otp_var, font=('TH Sarabun New', 16),
      width=20, bg="white").grid(row=2, column=1, padx=5, pady=5)

Button(frame_main, text="Confirm", font=('TH Sarabun New', 14, 'bold'),
       command=confirm_otp, bg="light blue").grid(row=2, column=2, padx=5, pady=5)

# ปุ่มสำหรับ Reset, Top Up และ Exit
Button(frame_main, text="Reset", font=('TH Sarabun New', 14, 'bold'),
       command=reset_fields, bg="orange").grid(row=3, column=1, padx=5, pady=20, sticky='e')
Button(frame_main, text="Exit", font=('TH Sarabun New', 14, 'bold'),
       command=exit_app, bg="tomato").grid(row=3, column=2, padx=5, pady=20, sticky='w')

# ปุ่มเปิดหน้าการเติมเงิน
Button(root, text="Top Up", font=('TH Sarabun New', 14, 'bold'),
       command=open_top_up_window, bg="light blue").pack(pady=5)

Label(root, textvariable=status_var, font=('TH Sarabun New', 16, 'bold'),
      fg="red", bg="seashell2").pack(pady=10)

reader = NFC_Reader()

card_id = reader.read_uid()

card_id_var.set(card_id)

print("Card id:", card_id)

root.mainloop()
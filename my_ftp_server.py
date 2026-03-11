from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer
import os

def main():
    # 1. จัดการ User (ชื่อผู้ใช้, รหัสผ่าน, โฟลเดอร์ที่เก็บไฟล์, สิทธิ์การใช้งาน)
    authorizer = DummyAuthorizer()
    
    # แก้ไข 'user', '12345' และ 'path/to/folder' ตามต้องการ
    # สิทธิ์ "elradfmwMT" คือให้ทำได้ทุกอย่าง (อ่าน เขียน ลบ สร้างโฟลเดอร์)
    authorizer.add_user("admin", "1234", ".", perm="elradfmwMT")
    
    # 2. ตั้งค่า Handler (ตัวจัดการคำสั่ง FTP)
    handler = FTPHandler
    handler.authorizer = authorizer
    
    # ปิด TLS (ใช้ Plain FTP ธรรมดา)
    handler.banner = "FTP Server is ready."

    # 3. ระบุ IP และ Port (0.0.0.0 คือยอมรับทุกการเชื่อมต่อ, Port 21 เป็นค่ามาตรฐาน)
    address = ("0.0.0.0", 2121)
    server = FTPServer(address, handler)

    # 4. เริ่มทำงาน
    print("\n[INFO] FTP Server กำลังรันอยู่ที่ Port 2121...")
    print("[HINT] กด Ctrl+C เพื่อปิดการทำงาน")
    
    try:
        # กำหนด timeout สั้นๆ เพื่อให้ Python สามารถดักจับ KeyboardInterrupt ได้ใน Windows
        server.serve_forever(timeout=1.0)
    except (KeyboardInterrupt, SystemExit):
        print("\n[INFO] ตรวจพบการกด Ctrl+C... กำลังปิด Server...")
        server.close_all()
        print("[INFO] Server ปิดเรียบร้อยแล้ว")
        os._exit(0) # บังคับปิด process ทันทีเพื่อไม่ให้ค้าง
    except Exception as e:
        print(f"\n[ERROR] เกิดข้อผิดพลาดกับ Server: {e}")

if __name__ == "__main__":
    main()
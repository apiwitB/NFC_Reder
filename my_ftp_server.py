from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

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
    print("Server กำลังวิ่งอยู่ที่ Port 21... กด Ctrl+C เพื่อปิด")
    server.serve_forever()

if __name__ == "__main__":
    main()
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

def main():
    server = None
    try:
        # 1. จัดการ User
        authorizer = DummyAuthorizer()
        authorizer.add_user("admin", "1234", ".", perm="elradfmwMT")
        
        # 2. ตั้งค่า Handler
        handler = FTPHandler
        handler.authorizer = authorizer
        handler.banner = "FTP Server is ready."

        # 3. ระบุ IP และ Port 2121 (0.0.0.0 เพื่อให้เครื่องอื่นเข้าได้)
        address = ("0.0.0.0", 2121)
        server = FTPServer(address, handler)

        print("-" * 50)
        print("FTP Server is starting...")
        print(f"Address : {address[0]}")
        print(f"Port    : {address[1]}")
        print("-" * 50)
        print("Username: admin | Password: 1234")
        print("Path    : Current Directory (.)")
        print("-" * 50)
        print("Press Ctrl+C to STOP / กด Ctrl+C เพื่อหยุดการทำงาน")
        
        # เริ่มทำงาน (เพิ่ม timeout เพื่อให้เช็ค KeyboardInterrupt ได้ไวขึ้น)
        server.serve_forever(timeout=1.0)


    except KeyboardInterrupt:
        print("\n[STOP] กำลังปิด Server ตามคำสั่งผู้ใช้...")
    except PermissionError:
        print("\n[ERROR] Permission Denied: ไม่ได้รับอนุญาตให้ใช้ Port นี้ หรือต้องใช้สิทธิ์ Admin")
    except OSError as e:
        if e.errno == 10048:
            print(f"\n[ERROR] Port {address[1]} ถูกใช้งานอยู่โดยโปรแกรมอื่น")
        else:
            print(f"\n[ERROR] System Error: {e}")
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
    finally:
        if server:
            print("Cleaning up sockets...")
            server.close_all()
        print("Server closed. / ปิดระบบเรียบร้อยแล้ว")

if __name__ == "__main__":
    main()


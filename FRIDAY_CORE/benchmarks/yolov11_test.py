# yolov11_test.py
from ultralytics import YOLO
import cv2

def run_yolo11_test():
    print("[*] Initializing YOLOv11 Engine...")
    # Load the YOLO11n model
    try:
        model = YOLO("yolo11n.pt") # Automatically downloads if missing
        print("[+] YOLOv11 Model Loaded successfully.")
        
        # Test inference on a sample image (or the first frame of the webcam)
        print("[*] Running test inference...")
        results = model.predict(source="https://ultralytics.com/images/bus.jpg", save=True, conf=0.5)
        
        for r in results:
            print(f"[!] Detection Summary: {len(r.boxes)} objects identified.")
            for box in r.boxes:
                cls = int(box.cls[0])
                name = model.names[cls]
                conf = float(box.conf[0])
                print(f"  - {name} ({conf:.2f})")
                
        print("\n[SUCCESS] YOLOv11 is fully operational on your system.")
        
    except Exception as e:
        print(f"[ERROR] YOLOv11 Test Failed: {e}")

if __name__ == "__main__":
    run_yolo11_test()

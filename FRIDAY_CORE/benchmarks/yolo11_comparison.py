# yolo11_comparison.py
from ultralytics import YOLO
import time
import cv2

def run_yolo11_performance_test():
    print("[*] YOLOv11 Performance Benchmark Initiated...")
    
    # 1. Load the YOLO11n model
    try:
        model = YOLO("yolo11n.pt") 
        print("[+] YOLOv11 Model Loaded.")
        
        # Sample image for inference (standard benchmark image)
        img_url = "https://ultralytics.com/images/bus.jpg"
        
        # Warm-up run (to ensure CUDA/CPU kernels are ready)
        print("[*] Warming up hardware...")
        model.predict(source=img_url, verbose=False)
        
        # 2. Timing the inference
        print("[*] Measuring inference speed...")
        start_time = time.time()
        results = model.predict(source=img_url, verbose=False)
        end_time = time.time()
        
        inference_time_ms = (end_time - start_time) * 1000
        
        # 3. Output results
        print("\n" + "="*40)
        print("YOLOv11 HARDWARE PERFORMANCE REPORT")
        print("="*40)
        print(f"Model: YOLO11n")
        print(f"Inference Time: {inference_time_ms:.2f} ms")
        print(f"FPS Equivalent: {1000/inference_time_ms:.1f}")
        
        for r in results:
            print(f"Objects Detected: {len(r.boxes)}")
        print("="*40)
        
        print("\n[SUCCESS] Performance benchmark complete, Sir.")
        
    except Exception as e:
        print(f"[ERROR] Comparison Test Failed: {e}")

if __name__ == "__main__":
    run_yolo11_performance_test()

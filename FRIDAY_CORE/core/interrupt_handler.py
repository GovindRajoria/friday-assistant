# core/interrupt_handler.py
from pynput import keyboard


class InterruptHandler:
    def __init__(self):
        self.interrupted = False
        self.listener = keyboard.Listener(on_press=self._on_press)
        self.listener.start()

    def _on_press(self, key):
        try:
            if key == keyboard.Key.delete:
                print("\n[!] EMERGENCY STOP: Interrupting cognitive loop...")
                self.interrupted = True
        except AttributeError:
            pass

    def reset(self):
        self.interrupted = False

    def stop(self):
        self.listener.stop()

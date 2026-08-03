# core/main.py
from core.config import PROJECT_ROOT, SETTINGS
from core.graph import build_graph
from core.interrupt_handler import InterruptHandler
from core.listener import FridayListener
from core.registry import discover_skills
from core.session import SPOKEN_EVENT_TYPES, run_turn
from core.speaker import FridaySpeaker


class FridayCore:
    def __init__(self, settings=None):
        self.settings = settings or SETTINGS
        self.speaker = FridaySpeaker(settings=self.settings)
        self.listener = FridayListener(settings=self.settings)

        # Announce boot sequence
        self.speaker.speak("Initializing core systems.")

        self.active_skills = discover_skills()
        self.graph = build_graph(self.active_skills, confirm=self._confirm_on_stdin)

        # Short-term conversational memory, carried across turns and fed into
        # each new request so follow-up questions have something to refer to.
        self.memory_buffer = []

        # Emergency Interrupt System
        self.interrupter = InterruptHandler()

        self.speaker.speak("All systems online. I am ready when you are.")

    def _confirm_on_stdin(self, action, action_input, thought):
        """Ask a human on stdin before a destructive action runs.

        The console has no other confirmation channel — this blocks the
        whole process on `input()`, exactly as everything else in the
        console loop already blocks on speech recognition between turns.
        Anything other than an explicit yes is a denial, including a blank
        line or Ctrl-C reaching EOFError: default-deny applies here too, not
        just when the callable is missing entirely.
        """
        self.speaker.speak(f"Confirmation required to run {action}.")
        print(f"\n[CONFIRM] {action} wants to run with {action_input}")
        if thought:
            print(f"  reasoning: {thought}")
        try:
            answer = input("Approve? [y/N]: ").strip().lower()
        except EOFError:
            answer = "n"
        return answer in ("y", "yes")

    def _run_graph(self, user_input, speak):
        """Drive the graph to completion, speaking its narration as it streams.

        A thin wrapper around core.session.run_turn — the turn-running logic
        (the None-delta skip, the interrupt early-return) lives there now so
        the WebSocket server can drive the same graph without duplicating it.
        This is still the single narration consumer on the console side —
        nodes never call the speaker directly. `speak=False` is used for
        batch testing, where only the final answer is logged, not spoken.
        """
        def emit(event_type, payload):
            if not speak or event_type not in SPOKEN_EVENT_TYPES:
                return
            text = payload.get("text")
            if text:
                self.speaker.speak(text)

        return run_turn(
            self.graph, user_input, self.memory_buffer, self.interrupter, emit,
            history_length=self.settings["llm"]["history_length"],
        )

    def run_continuous_voice_loop(self):
        wake_word = self.settings["assistant"]["wake_word"].upper()
        print("\n" + "="*50)
        print(f"[LIVE] Say '{wake_word}' to wake the assistant.")
        print("="*50 + "\n")

        while True:
            self.interrupter.reset() # Reset for new command

            # The listener now handles both Voice and Keyboard (Hybrid Input)
            user_input = self.listener.listen(require_wake_word=True)

            if user_input:
                print(f"\n[YOU] {user_input}")

                if "power down" in user_input or "go to sleep" in user_input:
                    self.speaker.speak("Powering down all systems. Goodbye.")
                    break

                if user_input == "yes?":
                    self.speaker.speak("I'm listening.")
                    user_input = self.listener.listen(require_wake_word=False)
                    if not user_input:
                        continue

                # Native Batch Test Trigger (Bypasses skill system for security)
                if "run the test suite" in user_input.lower() or "initiate system self-diagnostic" in user_input.lower():
                    self.run_batch_test()
                    continue

                print("[*] Suspending listener for cognitive processing...")
                self.memory_buffer.append(f"User: {user_input}")
                final_answer = self._run_graph(user_input, speak=True)
                self.memory_buffer.append(f"FRIDAY: {final_answer}")

                # Only the last `history_length` lines are ever read back, and
                # this process is meant to stay up for days. Drop everything
                # older than twice that rather than growing a list nothing
                # will look at again.
                del self.memory_buffer[:-2 * self.settings["llm"]["history_length"]]

    def run_batch_test(self):
        import datetime
        test_file = PROJECT_ROOT / "test_suite.txt"
        log_file = PROJECT_ROOT / "test_results.log"

        if not test_file.exists():
            self.speaker.speak("I couldn't find the test suite file.")
            return

        self.speaker.speak("Initiating automated batch testing.")
        with open(test_file, "r") as f:
            test_cases = [line.strip() for line in f if line.strip()]

        results = []
        for i, test_case in enumerate(test_cases):
            if self.interrupter.interrupted:
                self.speaker.speak("Batch testing terminated.")
                break

            print(f"\n[TEST {i+1}/{len(test_cases)}] Processing: {test_case}")
            batch_prompt = f"BATCH_TEST_MODE: {test_case}"

            final_answer = self._run_graph(batch_prompt, speak=False)
            results.append(f"CASE: {test_case}\nRESULT: {final_answer}\n" + "-"*30)

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_file, "a") as f:
            f.write(f"\n\n=== NATIVE TEST RUN: {timestamp} ===\n")
            f.write("\n".join(results))

        self.speaker.speak("Testing complete. Results logged.")

if __name__ == "__main__":
    friday = FridayCore()
    friday.run_continuous_voice_loop()

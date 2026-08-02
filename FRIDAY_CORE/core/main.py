# core/main.py
from core.config import PROJECT_ROOT, SETTINGS
from core.graph import build_graph
from core.interrupt_handler import InterruptHandler
from core.listener import FridayListener
from core.registry import discover_skills
from core.speaker import FridaySpeaker


class FridayCore:
    def __init__(self, settings=None):
        self.settings = settings or SETTINGS
        self.speaker = FridaySpeaker(settings=self.settings)
        self.listener = FridayListener(settings=self.settings)

        # Announce boot sequence
        self.speaker.speak("Initializing core systems.")

        self.active_skills = discover_skills()
        self.graph = build_graph(self.active_skills)

        # Short-term conversational memory, carried across turns and fed into
        # each new request so follow-up questions have something to refer to.
        self.memory_buffer = []

        # Emergency Interrupt System
        self.interrupter = InterruptHandler()

        self.speaker.speak("All systems online. I am ready when you are.")

    def _run_graph(self, user_input, speak):
        """Drive the graph to completion, streaming its narration.

        This is the single narration consumer — nodes never call the speaker
        directly, which is what keeps Phase 2's WebSocket fanout from ever
        speaking a line twice. `speak=False` is used for batch testing, where
        only the final answer is logged, not spoken.
        """
        history_length = self.settings["llm"]["history_length"]
        state = {
            "user_input": user_input,
            "memory_buffer": "\n".join(self.memory_buffer[-history_length:]),
            "messages": [],
            "steps": 0,
        }
        final_answer = ""

        for update in self.graph.stream(state, {"recursion_limit": 40}, stream_mode="updates"):
            if self.interrupter.interrupted:
                # Leave the flag set rather than resetting it here. The voice
                # loop resets it at the top of its own while-loop; batch
                # testing relies on it staying set so the outer test-case
                # loop sees it and stops the whole run, not just this case.
                if speak:
                    self.speaker.speak("Manual override. Thought process terminated.")
                return "Thought process terminated by emergency override."

            for delta in update.values():
                # A node that returns {} (anomaly_guard on the common "nothing
                # changed" path) streams as None under stream_mode="updates",
                # not {} — observed directly against langgraph 1.2.10, not
                # documented behavior. Skipping it here is required, not
                # defensive: without this check, a routine turn with no
                # anomaly crashes the driver.
                if not delta:
                    continue
                for line in delta.get("narration", []):
                    if speak:
                        self.speaker.speak(line)
                if delta.get("final_answer"):
                    final_answer = delta["final_answer"]

        if not final_answer:
            final_answer = "I worked through that but have no answer to give."
        if speak:
            self.speaker.speak(final_answer)
        return final_answer

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

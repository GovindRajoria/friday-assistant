# core/main.py
import importlib
import os
from pathlib import Path

from core.brain import FridayBrain
from core.config import PROJECT_ROOT, SETTINGS
from core.interrupt_handler import InterruptHandler
from core.listener import FridayListener
from core.speaker import FridaySpeaker


class FridayCore:
    def __init__(self, settings=None):
        self.settings = settings or SETTINGS
        self.active_skills = {}
        self.speaker = FridaySpeaker(settings=self.settings)
        self.listener = FridayListener(settings=self.settings)

        # Announce boot sequence
        self.speaker.speak("Initializing core systems.")

        self.load_antigravity_skills()
        self.brain = FridayBrain(settings=self.settings)
        
        # Emergency Interrupt System
        self.interrupter = InterruptHandler()
        
        self.speaker.speak("All systems online. I am ready when you are.")

    def load_antigravity_skills(self):
        print("[*] Initializing Antigravity Modules...")
        # Use absolute path relative to the project root
        project_root = Path(__file__).parent.parent
        skills_dir = project_root / "skills"
        
        for file_path in skills_dir.rglob("*.py"):
            if file_path.name == "__init__.py":
                continue
            
            # Convert absolute path to relative module name
            relative_path = file_path.relative_to(project_root)
            module_name = str(relative_path.with_suffix('')).replace(os.sep, '.')
            print(f"[*] Discovering: {module_name} at {file_path}")
            try:
                module = importlib.import_module(module_name)
                if hasattr(module, 'setup'):
                    skill_instance = module.setup()
                    skill_name = skill_instance.manifest['name']
                    self.active_skills[skill_name] = skill_instance
                    print(f"[+] Loaded: {skill_name}")
            except Exception as e:
                print(f"[-] Failed to load {module_name}: {e}")

    def run_continuous_voice_loop(self):
        wake_word = self.settings["assistant"]["wake_word"].upper()
        print("\n" + "="*50)
        print(f"[LIVE] Say '{wake_word}' to wake the assistant.")
        print("="*50 + "\n")

        # Initialize short-term conversational memory
        if not hasattr(self, 'memory_buffer'):
            self.memory_buffer = []

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

                # Add user input to short-term hippocampus
                self.memory_buffer.append(f"User: {user_input}")
                recent_context = "\n".join(self.memory_buffer[-10:])

                # 1. Trigger the Initial Brain Cycle
                print("[*] Suspending listener for cognitive processing...")
                decision = self.brain.analyze_intent(
                    user_prompt=user_input,
                    active_skills=self.active_skills,
                    memory_buffer=recent_context
                )

                # 2. The ReAct Ping-Pong Loop
                while decision.get("intent") not in ["final_answer", "unknown"]:
                    
                    # --- THE KILL SWITCH CHECK ---
                    if self.interrupter.interrupted:
                        self.speaker.speak("Manual override. Thought process terminated.")
                        decision = {"intent": "final_answer", "message": "Thought process terminated by emergency override."}
                        self.interrupter.reset()
                        break

                    # --- Speak the thought out loud before acting ---
                    if decision.get("thought"):
                        self.speaker.speak(decision.get("thought"))

                    intent = decision.get("intent")
                    params = decision.get("parameters", {})
                    react_messages = decision.get("react_messages", [])

                    print(f"\n[*] FRIDAY EXECUTING ACTION: {intent}")
                    print(f"[*] PARAMS: {params}")

                    # Execute the chosen tool
                    if intent in self.active_skills:
                        try:
                            tool_result = self.active_skills[intent].execute(params)
                            observation = tool_result.get("message", str(tool_result))
                        except Exception as e:
                            observation = f"Error executing tool: {str(e)}"
                    else:
                        observation = f"Error: The tool '{intent}' does not exist or failed to load."

                    print(f"[*] OBSERVATION: {observation}")

                    # Feed the observation back to the Brain
                    decision = self.brain.resume_react(react_messages, observation)

                # 3. Speak the Final Answer
                
                # --- Speak her very last thought before delivering the final answer ---
                if decision.get("thought"):
                    self.speaker.speak(decision.get("thought"))
                
                final_message = decision.get("message", "I experienced a critical failure in my cognitive loop.")
                self.speaker.speak(final_message)
                
                # Save FRIDAY's final response to short-term memory
                self.memory_buffer.append(f"FRIDAY: {final_message}")

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
            
            decision = self.brain.analyze_intent(batch_prompt, self.active_skills)
            while decision.get("intent") not in ["final_answer", "unknown"]:
                if self.interrupter.interrupted:
                    break
                
                intent = decision.get("intent")
                params = decision.get("parameters", {})
                react_messages = decision.get("react_messages", [])
                
                if intent in self.active_skills:
                    try:
                        tool_result = self.active_skills[intent].execute(params)
                        observation = tool_result.get("message", str(tool_result))
                    except Exception as e:
                        observation = f"Error: {str(e)}"
                else:
                    observation = f"Error: Tool {intent} not found."
                
                decision = self.brain.resume_react(react_messages, observation)

            final_answer = decision.get("message", "Logic processing failed.")
            results.append(f"CASE: {test_case}\nRESULT: {final_answer}\n" + "-"*30)

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_file, "a") as f:
            f.write(f"\n\n=== NATIVE TEST RUN: {timestamp} ===\n")
            f.write("\n".join(results))
        
        self.speaker.speak("Testing complete. Results logged.")

if __name__ == "__main__":
    friday = FridayCore()
    friday.run_continuous_voice_loop()

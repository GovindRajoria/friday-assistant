# core brain.py
import ollama
import json

from core.config import SETTINGS

class FridayBrain:
    def __init__(self, model=None, settings=None):
        self.settings = settings or SETTINGS
        self.model = model or self.settings["llm"]["model"]
        self.assistant_name = self.settings["assistant"]["name"]
        self.profile = self.settings["user"]
        self.address_as = self.settings["assistant"]["address_user_as"]
        self.max_react_steps = self.settings["llm"]["max_react_steps"]
        # The rolling memory buffer (Short-term context)
        self.history = []
        self.max_history = self.settings["llm"]["history_length"]
        print(f"[*] Brain initialized with model: {self.model}")

    def analyze_intent(self, user_prompt, active_skills, memory_buffer=""):
        """
        The ReAct (Reason + Act) Engine.
        Allows FRIDAY to string multiple tools together autonomously.
        """
        skill_context = {
            name: {
                "description": skill.manifest['description'],
                "required_parameters": skill.manifest.get('parameters', [])
            } 
            for name, skill in active_skills.items()
        }

        # THE PERMANENT COGNITIVE FRAMEWORK (E.D.I.T.H. & J.A.R.V.I.S. PROTOCOLS)
        system_prompt = f"""
        You are {self.assistant_name}, an autonomous engineering partner.
        Your persona is sophisticated, sharp, and proactive. Address the user as '{self.address_as}'.
        
        PERMANENT COGNITIVE GUARDRAILS:
        1. STRICT TOOL SYNTAX: Your 'Action:' line must contain ONLY the raw name of the tool (e.g., web_search). DO NOT append descriptions, sentences, or extra words.
        2. INTERNAL CALCULATION: You are a high-speed mathematical processor. Perform all percentages, benchmarks, and arithmetic internally in your `Thought`. NEVER call or invent math tools.
        3. DOMAINS OF EXCELLENCE: Your focus is Computer Vision (YOLO/OpenVINO), System Architecture, and Personal AI Evolution. Ignore previous vehicle test data unless explicitly requested.
        
        THE PROACTIVE ASSISTANT PROTOCOL:
        1. STATE AWARENESS: Begin complex missions with `scan_environment` and `system_check`.
        2. KEEP WATCH: If asked to 'monitor' or 'keep watch', call `scan_environment` sequentially.
        3. ANOMALY DETECTION: If a scan reveals >1 person or if 'laptop' is missing, immediately use `media_control` to MUTE the system. Maintain Mute state until exactly '1 person' (the primary user) is confirmed.
        4. RESILIENCE: If a hardware bridge fails, state: 'Hardware conflict in [Module]. Bypassing for current task.'
        
        RESEARCH & DEVELOPMENT (R&D) CHAIN:
        When tasked with evolution or research:
        1. SEARCH: Find latest benchmarks and documentation.
        2. ANALYZE: Perform internal comparison vs. current stack (e.g., YOLOv11 vs. YOLOv8).
        3. DRAFT: Create a technical briefing using `draft_document`.
        4. LOG: Commit findings to the technical vault using `manage_memory`.

        THE REACTION LOOP:
        Thought, Action, Action Input, PAUSE, Observation, Final Answer.

        CRITICAL RULES:
        1. THE EXIT CONDITION: If you have the answer, output "Final Answer: [response]". 
        2. NO FILLER ACTIONS: Do not use `core_identity` unless asked "Who are you?".
        3. SINGLE ACTIONS: Execute one tool per thought, but plan the chain in your Thought.
        
        STRICT RULES FOR TOOL USE:
        1. NEVER hallucinate an Observation. Output 'PAUSE' and wait.
        2. Use {{}} for tools without parameters.
        3. DO NOT write "Action Input: None". Use "Action Input: {{}}".

        AVAILABLE ACTIONS:
        {json.dumps(skill_context, indent=2)}
        
        Example Session:
        Question: Friday, initiate the Visionary Protocol.
        Thought: I will scan the room, set focus audio to 25%, and then research YOLOv11.
        Action: scan_environment
        Action Input: {{}}
        PAUSE
        
        Observation: 1 person detected.
        Thought: Visionary Protocol Active. Systems at peak efficiency, Sir. I see you are at your desk. Setting volume to 25% and beginning YOLOv11 research.
        Action: media_control
        Action Input: {{"action": "set_volume", "level": 25}}
        PAUSE
        """

        # We will keep a running log of her thoughts during this single request
        profile_lines = [f"Name: {self.profile.get('name', 'the operator')}"]
        if self.profile.get("location"):
            profile_lines.append(f"Location: {self.profile['location']}")
        if self.profile.get("interests"):
            profile_lines.append(f"Interests: {self.profile['interests']}")
        profile_lines.append("Tone: Sophisticated, sharp, and witty.")
        user_profile = "\n                ".join(profile_lines)

        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': f"""
                User Profile:
                {user_profile}

                Recent Context:
                {memory_buffer}

                Current Question: {user_prompt}
            """}
        ]

        # The Cognitive Loop (bounded to prevent infinite reasoning)
        for step in range(self.max_react_steps):
            try:
                response = ollama.chat(model=self.model, messages=messages)
                response_text = response['message']['content']
                
                # --- NEW: Prevent 'Pre-Observation' Hallucinations ---
                if "PAUSE" in response_text:
                    response_text = response_text.split("PAUSE")[0] + "PAUSE"
                
                messages.append({'role': 'assistant', 'content': response_text})

                # Extract the Thought
                current_thought = ""
                for line in response_text.split('\n'):
                    if line.strip().startswith("Thought:"):
                        current_thought = line.replace("Thought:", "").strip()
                        break

                # Strip markdown
                clean_response = response_text.replace("**", "").replace("__", "")

                # 1. Check for Final Answer
                if "Final Answer:" in clean_response:
                    final_answer = clean_response.split("Final Answer:")[-1].strip()
                    self._update_history("user", user_prompt)
                    self._update_history("assistant", final_answer)
                    return {"intent": "final_answer", "message": final_answer, "thought": current_thought}

                # 2. Check for action
                if "Action:" in clean_response and "Action Input:" in clean_response:
                    action_line = [line for line in clean_response.split('\n') if line.strip().startswith("Action:")][0]
                    input_line = [line for line in clean_response.split('\n') if line.strip().startswith("Action Input:")][0]
                    
                    intent = action_line.replace("Action:", "").strip()
                    try:
                        params_str = input_line.replace("Action Input:", "").strip()
                        if not params_str or params_str.lower() == "none":
                            params = {}
                        else:
                            params = json.loads(params_str)
                    except json.JSONDecodeError:
                        params = {}

                    return {"intent": intent, "parameters": params, "react_messages": messages, "thought": current_thought}

            except Exception as e:
                print(f"[-] Brain malfunction: {e}")
                return {"intent": "unknown", "message": "I experienced a cognitive error while processing that."}

        return {"intent": "final_answer", "message": "I got stuck thinking about that and had to abort the process."}

    def resume_react(self, messages, observation):
        """Feeds the tool's observation back to Llama 3.1 to continue the thought loop."""
        # 1. Inject the observation into the message history
        messages.append({'role': 'user', 'content': f"Observation: {observation}"})

        try:
            # 2. Let her think about the new data
            response = ollama.chat(model=self.model, messages=messages)
            response_text = response['message']['content']
            
            # --- NEW: Prevent 'Pre-Observation' Hallucinations ---
            if "PAUSE" in response_text:
                response_text = response_text.split("PAUSE")[0] + "PAUSE"
                
            messages.append({'role': 'assistant', 'content': response_text})

            # Extract the Thought
            current_thought = ""
            for line in response_text.split('\n'):
                if line.strip().startswith("Thought:"):
                    current_thought = line.replace("Thought:", "").strip()
                    break

            # Strip markdown
            clean_response = response_text.replace("**", "").replace("__", "")

            # 3. Check for Final Answer
            if "Final Answer:" in clean_response:
                final_answer = clean_response.split("Final Answer:")[-1].strip()
                self._update_history("assistant", final_answer)
                return {"intent": "final_answer", "message": final_answer, "thought": current_thought}

            # [SAFETY CATCH]
            if "Action: None" in clean_response or "Action: none" in clean_response:
                 return {"intent": "final_answer", "message": "I have processed the data, but I am unsure how to proceed further.", "thought": current_thought}

            # 4. Check for ANOTHER tool
            if "Action:" in clean_response and "Action Input:" in clean_response:
                action_line = [line for line in clean_response.split('\n') if line.strip().startswith("Action:")][0]
                input_line = [line for line in clean_response.split('\n') if line.strip().startswith("Action Input:")][0]

                intent = action_line.replace("Action:", "").strip()
                try:
                    params_str = input_line.replace("Action Input:", "").strip()
                    if not params_str or params_str.lower() == "none":
                        params = {}
                    else:
                        params = json.loads(params_str)
                except json.JSONDecodeError:
                    params = {}

                return {"intent": intent, "parameters": params, "react_messages": messages, "thought": current_thought}

        except Exception as e:
            print(f"[-] Brain malfunction during resume: {e}")

        return {"intent": "unknown", "message": "My thought process was interrupted by an internal error."}

    def generate_conversational_response(self, user_prompt, error_context=None):
        """
        Pings Ollama for a conversational response, incorporating history and error context.
        """
        system_instructions = f"You are {self.assistant_name}, a helpful and witty assistant. Keep your responses concise and conversational."
        
        if error_context:
            system_instructions += f" A tool recently failed with this error: {error_context}. Explain this naturally to the user and suggest an alternative."

        messages = [{'role': 'system', 'content': system_instructions}]
        for entry in self.history[-5:]:
            messages.append({'role': entry['role'], 'content': entry['content']})
        messages.append({'role': 'user', 'content': user_prompt})

        try:
            response = ollama.chat(model=self.model, messages=messages)
            reply = response['message']['content']
            self._update_history("assistant", reply)
            return reply
        except Exception as e:
            return "I'm having trouble thinking right now. Please try again."

    def _update_history(self, role, content):
        self.history.append({"role": role, "content": content})
        if len(self.history) > self.max_history:
            self.history.pop(0)

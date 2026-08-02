# skills/utility/memory_manager.py
import datetime
import sqlite3

from core import llm_client
from core.config import PROJECT_ROOT, SETTINGS


class MemoryManagerSkill:
    def __init__(self):
        self.manifest = {
            "name": "manage_memory",
            "description": "Stores or retrieves permanent facts. 'action' MUST be 'store' or 'retrieve'. 'topic' MUST be a SINGLE, broad keyword (e.g., 'deployments', 'vids', 'brother'). NEVER use multiple words for the topic. 'fact_data' MUST be the ENTIRE complete sentence the user wants remembered.",
            "parameters": ["action", "topic", "fact_data"]
        }
        # Anchor to the project root so the vault does not follow the cwd.
        self.db_path = str(PROJECT_ROOT / "memory.db")
        self._init_db()

    def _init_db(self):
        """Creates the vault if it doesn't exist."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS memories
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      topic TEXT,
                      fact TEXT,
                      timestamp TEXT)''')
        conn.commit()
        conn.close()

    def execute(self, params=None):
        if not params or "action" not in params:
            return {"status": "error", "message": "I need to know whether to store or retrieve a memory."}

        action = params["action"].lower()
        topic = params.get("topic", "general").lower()

        if action == "store":
            fact = params.get("fact_data")
            if not fact:
                return {"status": "error", "message": "I need the actual fact to store it."}

            # Debugging print so you can see exactly what Llama extracted
            print(f"[*] STORING TO VAULT -> Topic: '{topic}' | Fact: '{fact}'")

            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            c.execute("INSERT INTO memories (topic, fact, timestamp) VALUES (?, ?, ?)", (topic, fact, timestamp))
            conn.commit()
            conn.close()
            
            return {"status": "success", "message": "I have securely committed that to my long-term memory."}

        elif action == "retrieve":
            print(f"[*] RETRIEVING FROM VAULT -> Searching for topic: '{topic}'")
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            search_term = f'%{topic}%'
            c.execute("SELECT fact FROM memories WHERE topic LIKE ? OR fact LIKE ?", (search_term, search_term))
            results = c.fetchall()
            conn.close()

            if not results:
                return {"status": "success", "message": "I searched my archives, but I don't have any memories stored regarding that."}

            # Combine all found memories
            raw_memories = " ".join([r[0] for r in results])
            print(f"[*] Raw facts found: {raw_memories}")

            # Synthesize a natural, human-like response using Llama 3.1
            print("[*] Synthesizing natural response...")
            synthesis_prompt = f"""
            You are {SETTINGS["assistant"]["name"]}. The user just asked you to remember something from your memory banks.
            Use the following raw database facts to answer their question in a natural, conversational 1-2 sentence reply.
            
            Raw Facts: {raw_memories}
            """
            
            summary = llm_client.chat([
                {'role': 'user', 'content': synthesis_prompt}
            ]).strip()
            return {"status": "success", "message": summary}

        else:
            return {"status": "error", "message": "Invalid memory action. Must be 'store' or 'retrieve'."}

def setup():
    return MemoryManagerSkill()

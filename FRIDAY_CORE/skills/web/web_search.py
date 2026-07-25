# skills/web/web_search.py
import requests
from bs4 import BeautifulSoup
import ollama

from core.config import SETTINGS

class WebSearchSkill:
    def __init__(self):
        self.manifest = {
            "name": "web_search",
            "description": "Searches the internet for information. Use this specifically to find weather forecasts, news, current events, or to answer factual questions. DO NOT use this to open applications.",
            "parameters": ["search_query"]
        }

    def execute(self, params=None):
        if not params or "search_query" not in params:
            return {"status": "error", "message": "I need a specific query to search the web."}
            
        query = params["search_query"]
        print(f"[*] FRIDAY is searching the web for: '{query}'...")
        
        try:
            # 1. Query DuckDuckGo Lite directly using standard HTTP requests
            url = "https://lite.duckduckgo.com/lite/"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            # DDG Lite uses a POST request for searches
            data = {"q": query}
            
            response = requests.post(url, headers=headers, data=data, timeout=10)
            response.raise_for_status()
            
            # 2. Parse the HTML to extract just the answer snippets
            soup = BeautifulSoup(response.text, 'html.parser')
            snippets = []
            
            # DDG Lite stores the text summaries in table data cells with the class 'result-snippet'
            for td in soup.find_all('td', class_='result-snippet'):
                snippets.append(td.get_text(strip=True))
                
            if not snippets:
                return {"status": "error", "message": "I couldn't find any results for that query on the web."}
                
            # Grab the top 3 snippets to feed to the Brain
            context_chunk = "\n".join(snippets[:3])
            
            # 3. Summarize the findings using Llama 3.1
            print("[*] Analyzing scraped data...")
            summary_prompt = f"""
            You are a strict data extraction tool. 
            Based on the following raw web search snippets, extract ONLY the factual answer to the query: "{query}".
            DO NOT use conversational filler. DO NOT say "Hey there" or "Here is the info". 
            Just return the raw facts in 1-2 sentences. If the answer is not in the text, reply "Information not found."
            
            Raw Web Text:
            {context_chunk}
            """
            
            ai_response = ollama.chat(model=SETTINGS["llm"]["model"], messages=[
                {'role': 'user', 'content': summary_prompt}
            ])
            
            summary = ai_response['message']['content'].strip()
            return {"status": "success", "message": summary}

        except Exception as e:
            return {"status": "error", "message": f"My web search encountered an error: {str(e)}"}

def setup():
    return WebSearchSkill()

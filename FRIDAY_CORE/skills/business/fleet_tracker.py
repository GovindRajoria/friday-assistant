# skills/business/fleet_tracker.py
import os
import csv
from datetime import datetime

class FleetTrackerSkill:
    def __init__(self):
        self.manifest = {
            "name": "log_fleet_market_data",
            "description": "Logs real-time vehicle market prices to a CSV ledger for profitability forecasting. Parameters: 'vehicle_model' (str), 'average_price_lakhs' (float), 'location' (str), 'notes' (str).",
            "parameters": ["vehicle_model", "average_price_lakhs", "location", "notes"]
        }
        # Use absolute path for reliability
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        self.filepath = os.path.join(project_root, "fleet_market_ledger.csv")
        self._ensure_csv_exists()

    def _ensure_csv_exists(self):
        if not os.path.exists(self.filepath):
            with open(self.filepath, mode='w', newline='', encoding='utf-8') as file:
                writer = csv.writer(file)
                writer.writerow(["Timestamp", "Vehicle Model", "Location", "Avg Price (Lakhs)", "Notes"])

    def execute(self, params):
        model = params.get("vehicle_model", "Unknown")
        price = params.get("average_price_lakhs", 0.0)
        location = params.get("location", "Delhi NCR")
        notes = params.get("notes", "")
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            with open(self.filepath, mode='a', newline='', encoding='utf-8') as file:
                writer = csv.writer(file)
                writer.writerow([timestamp, model, price, location, notes])
            return {"status": "success", "message": f"Successfully logged {model} at {price} Lakhs to the fleet ledger."}
        except Exception as e:
            return {"status": "error", "message": f"Failed to write to CSV: {str(e)}"}

def setup():
    return FleetTrackerSkill()

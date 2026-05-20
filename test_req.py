import urllib.request
import json
url = "http://127.0.0.1:8000/v1/recommend"
payload = {"country": "LU", "driver_age": 32, "years_licensed": 12, "vehicle_value_eur": 22000, "vehicle_power_kw": 95, "vehicle_age_years": 4, "annual_mileage_km": 12000, "claims_last_5y": 0, "usage_type": "private", "coverage_preference": "standard"}
data = json.dumps(payload).encode("utf-8")
req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req) as response:
        print(f"Status: {response.getcode()}")
        print(f"Body: {response.read().decode()}")
except Exception as e:
    print(f"Error: {e}")
    if hasattr(e, 'read'): print(e.read().decode())

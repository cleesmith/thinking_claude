from anthropic import Anthropic
import re
from datetime import datetime

# Initialize client - automatically uses ANTHROPIC_API_KEY from environment
client = Anthropic()

response = client.models.list()

# Function to extract date from model name (if present)
def extract_date(model_str):
    # Look for dates in format YYYYMMDD
    date_match = re.search(r'(\d{8})', str(model_str))
    if date_match:
        date_str = date_match.group(1)
        try:
            # Convert to datetime object for sorting
            return datetime.strptime(date_str, '%Y%m%d')
        except ValueError:
            pass
    
    # If no date found or couldn't parse it, return a minimum date
    # (this will place models without dates at the end)
    return datetime.min

# sort models by date (newest first)
# use str(model) to get a string representation that should contain the model name
sorted_models = sorted(response.data, key=lambda model: extract_date(str(model)), reverse=True)

print("\nAvailable Anthropic Models (Newest to Oldest):\n")
for model in sorted_models:
    print("-" * 60)
    print(f"Model: {model}")
    
    # Try to extract and display the date in a readable format
    date_match = re.search(r'(\d{8})', str(model))
    if date_match:
        date_str = date_match.group(1)
        try:
            model_date = datetime.strptime(date_str, '%Y%m%d')
            print(f"Released: {model_date.strftime('%B %d, %Y')}")
        except ValueError:
            pass

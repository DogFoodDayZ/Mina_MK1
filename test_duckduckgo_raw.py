import requests
import json

# Test raw DuckDuckGo API
url = "https://api.duckduckgo.com/?q=python&format=json"
response = requests.get(url, timeout=10)
data = response.json()

print("API Response Keys:")
print(list(data.keys()))
print()

print("AbstractText:", data.get("AbstractText", "NONE")[:100] if data.get("AbstractText") else "None")
print("Heading:", data.get("Heading", "NONE"))
print("AbstractURL:", data.get("AbstractURL", "NONE"))
print()

print("RelatedTopics count:", len(data.get("RelatedTopics", [])))
if data.get("RelatedTopics"):
    print("First RelatedTopic:", data.get("RelatedTopics")[0])
print()

print("Results count:", len(data.get("Results", [])))
if data.get("Results"):
    print("First Result:", data.get("Results")[0])

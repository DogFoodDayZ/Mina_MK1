#!/usr/bin/env python3
"""Countdown demo - proven to work via Copilot file write."""
import time

def countdown(start=10):
    """Count down from start to 1."""
    for i in range(start, 0, -1):
        print(f"{i}...")
        time.sleep(0.5)
    print("Done!")

if __name__ == "__main__":
    countdown(10)

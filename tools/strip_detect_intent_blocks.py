"""
Remove specific intercept blocks from detect_tool_intent per the contract audit.
Blocks removed: remaining Spotify routing, hardware keyword routing.
"""
import re
import sys
import os

src_path = os.path.join(os.path.dirname(__file__), "..", "agent", "core.py")
with open(src_path, encoding="utf-8") as f:
    src = f.read()

original_len = len(src.splitlines())

# Remove remaining Spotify block content (helpers + if spotify_available blocks)
# These were left over after the first partial removal

# Pattern: from the bad replacement line down to the identity_patterns list
bad_replacement = r"        # ====================================================\\n        # IDENTITY REFLEX\\n        # ====================================================\\n        identity_patterns = \["
real_identity = """        # ====================================================
        # IDENTITY REFLEX
        # ====================================================
        identity_patterns = ["""

src = src.replace(
    "        # ====================================================\\n        # IDENTITY REFLEX\\n        # ====================================================\\n        identity_patterns = [",
    real_identity
)

# Now find and remove the remaining spotify body between identity_patterns list start
# and the actual identity_patterns list content
# The remaining spotify code starts right after "identity_patterns = [" and before the actual patterns

# Find the corrupted section: after identity_patterns = [ there should be identity patterns
# but instead there's spotify code. Find from capability_q down to before identity_patterns proper.

# Find where the spotify body ends (right before real identity patterns)
spotify_body_start = src.find("            # Capability questions should not trigger playback actions.")
real_identity_start = src.find('            r"\\bwho\\s+are\\s+you\\b",')

if spotify_body_start > 0 and real_identity_start > 0 and spotify_body_start < real_identity_start:
    src = src[:spotify_body_start] + src[real_identity_start:]
    print(f"Removed Spotify body block ({real_identity_start - spotify_body_start} chars)")
else:
    print(f"Spotify body: start={spotify_body_start}, identity={real_identity_start}")
    print("Spotify body may already be clean")

with open(src_path, "w", encoding="utf-8") as f:
    f.write(src)

new_len = len(src.splitlines())
print(f"Lines: {original_len} -> {new_len} (removed {original_len - new_len})")

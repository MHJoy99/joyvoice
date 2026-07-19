import requests
import base64
import json
import os

API_URL = "https://ai.bdx.market/v1/images/generations"
API_KEY = "40e0e9c930ace36fba1c3917bef45685d1161921bf63c94a00d9f0cc51a2608a"
ASSETS_DIR = r"C:\Users\Administrator\VoiceFloat\joyvoice\assets"
MODEL = "gpt-image-2"

os.makedirs(ASSETS_DIR, exist_ok=True)

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

images = [
    {
        "filename": "hero-banner.png",
        "prompt": (
            "Dark-themed hero banner with a sleek floating modern microphone in the center, "
            "cyan and turquoise neon glow radiating from the mic, dark gradient background "
            "(deep navy to near black), text 'JoyVoice - Speak Bengali, Get English' in bold "
            "modern sans-serif typography arranged elegantly. Professional, cinematic, 4K quality, "
            "suitable for a GitHub README header. No watermark, no logo clutter."
        )
    },
    {
        "filename": "app-mockup.png",
        "prompt": (
            "A modern desktop app screenshot mockup showing a floating circular microphone button "
            "in the center of a clean dark-mode desktop interface. The mic button has a subtle cyan "
            "glow pulse ring around it. Minimal and clean design, modern UI aesthetic, soft shadows, "
            "dark gray background. The button looks like a professional voice-input app UI element. "
            "No text clutter, just the floating circular mic button as the focal point. "
            "High quality, suitable for GitHub repo preview."
        )
    },
    {
        "filename": "tech-pattern-divider.png",
        "prompt": (
            "Dark-themed abstract tech pattern with cyan and turquoise accents, featuring a "
            "stylized microphone audio waveform visualization. Geometric tech lines, subtle "
            "grid patterns, glowing cyan waveform bars representing voice audio. Dark gradient "
            "background transitioning from deep navy to black. Futuristic, clean, suitable as "
            "a section divider or background for a tech GitHub repo. No text. Seamless-tile "
            "compatible style, abstract and elegant."
        )
    },
]

for img in images:
    print(f"\nGenerating: {img['filename']}...")
    payload = {
        "model": MODEL,
        "prompt": img["prompt"],
        "n": 1,
        "size": "1024x1024",
        "response_format": "b64_json"
    }

    try:
        resp = requests.post(API_URL, headers=headers, json=payload, timeout=120)
        print(f"  Status: {resp.status_code}")

        if resp.status_code == 200:
            data = resp.json()
            b64 = data["data"][0]["b64_json"]
            img_bytes = base64.b64decode(b64)

            filepath = os.path.join(ASSETS_DIR, img["filename"])
            with open(filepath, "wb") as f:
                f.write(img_bytes)

            size_kb = len(img_bytes) / 1024
            print(f"  Saved: {filepath} ({size_kb:.1f} KB)")
        else:
            print(f"  ERROR: {resp.status_code}")
            print(f"  Response: {resp.text[:500]}")
    except Exception as e:
        print(f"  EXCEPTION: {e}")

print("\nDone.")

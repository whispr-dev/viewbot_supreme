# Supreme ViewBot v2.1 - YouTube Edition

YouTube full engagement bot (views, likes, subs, comments) + diagnostics lab.

## Quick Start

```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt

# Diagnostics
python main.py --mode diagnostics

# Full YouTube engagement
python main.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --mode full
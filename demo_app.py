"""
Entry point for the HOSTED test copy (for example on Streamlit Community Cloud).

It runs the same app as app.py in the fictional demo workspace only: it never opens local_data/,
and anything uploaded stays in memory for that browser session. Uploads are allowed for testing
with fictional or dummy files, behind a confirmation, and only for the six fictional hotels
(real company files are rejected as unknown properties; .xlsm workbooks are refused).
When deploying, choose demo_app.py (not app.py) as the main file.
"""
from app import main

main(hosted_demo=True)

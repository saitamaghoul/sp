RINL Workforce Intelligence (single-file Flask app)

Quick start

1. Create a virtualenv and install requirements:

```bash
python -m venv venv
venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

2. Run:

```bash
python appv4.py
```

3. Open http://127.0.0.1:5000/

Features added in this version:
- Fixed CSV export and added per-department CSV downloads
- Equipment issue reporting + alerts
- Retired experts registry (consultant retention)
- Equipment-issues chart in dashboard

APIs:
- GET `/api/data`
- GET `/api/export/csv`
- GET `/api/export/csv/<dept>`
- POST `/api/equipment/report`
- GET `/api/equipment/<dept>`
- GET `/api/equipment/stats`
- POST `/api/retirements` and GET `/api/retirements/<dept>`

Notes
- This repository is a single-file demo. For production, split into modules and secure endpoints.

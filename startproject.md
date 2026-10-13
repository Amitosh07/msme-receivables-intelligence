# Terminal 1
cd "C:\Users\Amitosh Nigam\Desktop\msme-receivables-intelligence"
docker compose up -d redis


# Terminal 2
cd "C:\Users\Amitosh Nigam\Desktop\msme-receivables-intelligence"
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000


# Terminal 3
cd "C:\Users\Amitosh Nigam\Desktop\msme-receivables-intelligence"
.\.venv\Scripts\python.exe -m backend.app.workers.runtime


# Terminal 4
cd "C:\Users\Amitosh Nigam\Desktop\msme-receivables-intelligence\frontend"
npm run dev
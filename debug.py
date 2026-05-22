from fastapi.testclient import TestClient
from prometheus.api.main import app, settings
import jwt
token = jwt.encode({"sub": "user123"}, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
client = TestClient(app)
response = client.get("/api/portfolio/snapshot", headers={"Authorization": f"Bearer {token}"})
print(response.status_code)
print(response.json())

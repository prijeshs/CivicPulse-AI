import os
import pytest
from app import app, db, User, Grievance, analyze_grievance

@pytest.fixture()
def client(tmp_path):
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False,
        SQLALCHEMY_DATABASE_URI="sqlite:///" + str(tmp_path / "test.db"),
        SECRET_KEY="test-secret")
    with app.app_context():
        db.drop_all()
        db.create_all()
        citizen = User(name="Test Citizen", email="citizen@example.com",
                       password_hash="unused", role="citizen")
        officer = User(name="Test Officer", email="officer@example.com",
                       password_hash="unused", role="officer")
        db.session.add_all([citizen, officer])
        db.session.commit()
        yield app.test_client()
        db.session.remove()
        db.drop_all()

def test_triage_routes_water_and_detects_urgency():
    category, department, priority, reason = analyze_grievance(
        "Urgent water leak", "Emergency water pipeline leak near school")
    assert category == "Water Supply"
    assert department == "Municipal Water Board"
    assert priority == "Critical"
    assert "Urgency" in reason

def test_home_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"CivicResolve" in response.data

def test_citizen_cannot_open_officer_dashboard(client):
    with client.session_transaction() as sess:
        sess["user_id"] = 1
    response = client.get("/officer")
    assert response.status_code == 403

def test_officer_can_open_dashboard(client):
    with client.session_transaction() as sess:
        sess["user_id"] = 2
    response = client.get("/officer")
    assert response.status_code == 200
    assert b"Department action desk" in response.data
